from __future__ import annotations

import csv
import io
import time
import uuid
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, OuterRef, ProtectedError, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import (
    CreateView, DeleteView, DetailView, ListView, UpdateView,
)

from accounts.permissions import PermRequiredMixin, require_perm
from audit.models import Azione
from audit.utils import log as audit_log

from .forms import ClienteForm, ContattoForm
from .models import Cliente, Contatto, EsitoImportazioneClienti, Settore, StatoCliente
from .ricerca import CAMPI_CONTATTO, filtra_clienti, filtro_parole, normalizza_query


def con_fatturato(qs):
    """Annota ``fatturato`` (fatture − note di credito, imponibile) su ogni cliente."""
    from documenti.models import TestataFattura, TestataNotaCredito

    zero = Value(Decimal('0'), output_field=DecimalField(max_digits=14, decimal_places=2))

    def somma(model):
        return Coalesce(Subquery(
            model.objects.filter(cliente=OuterRef('pk')).order_by()
            .values('cliente').annotate(s=Sum('imponibile')).values('s')[:1],
        ), zero)

    return qs.annotate(fatturato=somma(TestataFattura) - somma(TestataNotaCredito))


class ClienteListView(LoginRequiredMixin, ListView):
    model = Cliente
    template_name = 'clienti/list.html'
    context_object_name = 'clienti'
    paginate_by = 8

    def get_queryset(self):
        qs = con_fatturato(
            Cliente.objects.all().select_related('account_manager__profile', 'settore')
            .prefetch_related('contatti'),
        )
        return filtra_clienti(qs, self.request.GET)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'clienti'
        ctx['q'] = normalizza_query(self.request.GET.get('q'))
        ctx['stati_attivi'] = self.request.GET.getlist('stato')
        ctx['settori_attivi'] = self.request.GET.getlist('settore')
        from django.utils import timezone
        now = timezone.localdate()
        ctx['tot_clienti'] = Cliente.objects.count()
        ctx['tot_nuovi_mese'] = Cliente.objects.filter(
            created_at__year=now.year, created_at__month=now.month,
        ).count()
        ctx['stati_choices'] = StatoCliente.choices
        # Mostra solo i settori effettivamente usati da almeno un cliente
        ctx['settori_choices'] = Settore.objects.filter(clienti__isnull=False).distinct().order_by('nome')
        ctx['view_mode'] = self.request.GET.get('view', 'list')
        return ctx

    def get_template_names(self):
        if self.request.htmx and not self.request.htmx.boosted:
            return ['clienti/_list_table.html']
        return [self.template_name]


class ClienteRowsPartial(LoginRequiredMixin, ListView):
    """Partial for HTMX reloads of the clienti table only."""
    model = Cliente
    template_name = 'clienti/_list_table.html'
    context_object_name = 'clienti'
    paginate_by = 8

    def get_queryset(self):
        return ClienteListView.get_queryset(self)


class ClienteDetailView(LoginRequiredMixin, DetailView):
    model = Cliente
    template_name = 'clienti/detail.html'
    context_object_name = 'cliente'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        c: Cliente = self.object
        ctx['active_nav'] = 'clienti'
        ctx['active_tab'] = self.request.GET.get('tab', 'panoramica')
        ctx['contatti'] = c.contatti.all()
        ctx['opportunita_attive'] = c.opportunita.exclude(
            stadio__in=['chiusa_win', 'chiusa_lost'],
        ).select_related('owner__profile')
        ctx['opportunita_count'] = c.opportunita.count()
        ctx['prossime_attivita'] = c.attivita.filter(completata=False).order_by('data')[:5]
        ctx['tasso_chiusura'] = c.tasso_chiusura
        ctx['fatturato_totale'] = c.fatturato_totale
        ctx['valore_opportunita'] = sum((o.valore or 0 for o in ctx['opportunita_attive']), 0)
        from accounts.permissions import has_perm
        ctx['vede_documenti'] = has_perm(self.request.user, 'documenti.vedi')
        if ctx['vede_documenti'] and ctx['active_tab'] in ('panoramica', 'documenti'):
            from documenti.estratto_conto import estratto_conto
            ctx['ec'] = estratto_conto(c)
        return ctx


class ClienteCreateView(PermRequiredMixin, LoginRequiredMixin, CreateView):
    required_perm = 'clienti.modifica'
    model = Cliente
    form_class = ClienteForm
    template_name = 'clienti/form.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'clienti'
        ctx['is_create'] = True
        return ctx

    def form_valid(self, form):
        messages.success(self.request, 'Cliente creato con successo')
        return super().form_valid(form)


class ClienteUpdateView(PermRequiredMixin, LoginRequiredMixin, UpdateView):
    required_perm = 'clienti.modifica'
    model = Cliente
    form_class = ClienteForm
    template_name = 'clienti/form.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'clienti'
        ctx['is_create'] = False
        return ctx

    def get_initial(self):
        initial = super().get_initial()
        primary = self.object.contatti.filter(primary=True).first()
        if primary:
            initial.update({
                'ref_nome': primary.nome,
                'ref_cognome': primary.cognome,
                'ref_email': primary.email,
                'ref_tel': primary.telefono,
            })
        return initial

    def form_valid(self, form):
        messages.success(self.request, 'Cliente aggiornato')
        return super().form_valid(form)


class ClienteDeleteView(PermRequiredMixin, LoginRequiredMixin, DeleteView):
    required_perm = 'clienti.elimina'
    model = Cliente
    success_url = reverse_lazy('clienti:list')
    template_name = 'clienti/confirm_delete.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'clienti'
        return ctx

    def form_valid(self, form):
        try:
            response = super().form_valid(form)
        except ProtectedError:
            messages.error(
                self.request,
                f'Impossibile eliminare «{self.object.ragione_sociale}»: '
                'ci sono documenti (offerte, ordini, DDT, fatture…) collegati. '
                'Puoi impostare il cliente come inattivo.',
            )
            return HttpResponseRedirect(self.object.get_absolute_url())
        messages.success(self.request, 'Cliente eliminato.')
        return response


def valida_partita_iva(request):
    """Async endpoint called on blur of P.IVA field.

    Returns JSON { valid, message } used by Alpine/HTMX in the form.
    """
    raw = (request.POST.get('partita_iva') or request.GET.get('partita_iva') or '').strip().upper()
    piva = raw[2:] if raw.startswith('IT') else raw
    if not piva.isdigit() or len(piva) != 11:
        return JsonResponse({'valid': False, 'message': 'Formato non valido: 11 cifre'})
    try:
        from stdnum.it import iva as stdnum_iva
        stdnum_iva.validate(piva)
    except Exception as exc:
        return JsonResponse({'valid': False, 'message': f'Non valida: {exc}'})
    return JsonResponse({'valid': True, 'message': f'IT{piva} — formato corretto'})


class ContattoListView(LoginRequiredMixin, ListView):
    model = Contatto
    template_name = 'clienti/contatti_list.html'
    context_object_name = 'contatti'
    paginate_by = 20

    def get_queryset(self):
        qs = Contatto.objects.select_related('cliente').order_by('-primary', 'cognome', 'nome', 'pk')
        q = normalizza_query(self.request.GET.get('q'))
        if q:
            qs = qs.filter(filtro_parole(q, *CAMPI_CONTATTO))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'contatti'
        ctx['q'] = normalizza_query(self.request.GET.get('q'))
        return ctx


class ContattoCreateView(PermRequiredMixin, LoginRequiredMixin, CreateView):
    required_perm = 'clienti.modifica'
    model = Contatto
    form_class = ContattoForm
    template_name = 'clienti/contatto_form.html'

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.cliente = None

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.cliente = get_object_or_404(Cliente, pk=kwargs['cliente_pk'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.cliente = self.cliente
        return super().form_valid(form)

    def get_success_url(self):
        return self.cliente.get_absolute_url() + '?tab=contatti'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'clienti'
        ctx['cliente'] = self.cliente
        return ctx


class ContattoDeleteView(PermRequiredMixin, LoginRequiredMixin, DeleteView):
    required_perm = 'clienti.elimina'
    model = Contatto
    http_method_names = ['post']

    def get_success_url(self):
        next_url = self.request.POST.get('next') or self.request.GET.get('next')
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={self.request.get_host()},
        ):
            return next_url
        return reverse_lazy('clienti:contatti_list')


CSV_HEADERS = [
    'ragione_sociale', 'tipo', 'settore',
    'partita_iva', 'codice_fiscale', 'codice_sdi', 'pec',
    'indirizzo', 'cap', 'citta', 'provincia', 'nazione',
    'stato', 'note',
]

# Etichette human-readable per il campo target nella UI di mapping
TARGET_FIELD_LABELS = {
    'ragione_sociale': 'Ragione sociale *',
    'partita_iva':     'Partita IVA *',
    'tipo':            'Tipo (azienda/privato/ente)',
    'settore':         'Settore',
    'codice_fiscale':  'Codice fiscale',
    'codice_sdi':      'Codice SDI',
    'pec':             'PEC',
    'indirizzo':       'Indirizzo',
    'cap':             'CAP',
    'citta':           'Città',
    'provincia':       'Provincia',
    'nazione':         'Nazione (sigla ISO)',
    'stato':           'Stato (attivo/prospect/…)',
    'note':            'Note',
}


MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_CSV_ROWS = 2000
MAX_CSV_COLUMNS = 100
IMPORT_TTL_SECONDS = 60 * 60
SESSION_KEY = '_csv_import_payload'


def _csv_decode_and_parse(raw_bytes: bytes):
    """Legge un file limitato senza cambiare i dati del CRM."""
    if len(raw_bytes) > MAX_CSV_BYTES:
        raise ValueError('Il file supera il limite di 2 MB.')
    try:
        decoded = raw_bytes.decode('utf-8-sig')
    except UnicodeDecodeError:
        decoded = raw_bytes.decode('latin-1')
    if '\x00' in decoded:
        raise ValueError('Il file contiene caratteri non validi. Esportalo in formato CSV.')
    try:
        dialect = csv.Sniffer().sniff(decoded[:8192], delimiters=',;\t')
    except csv.Error:
        # Una riga incompleta non deve impedire di riconoscere un CSV italiano
        # o TSV: la validazione delle singole righe avviene nell'anteprima.
        try:
            dialect = csv.Sniffer().sniff(decoded.splitlines()[0] if decoded else '', delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
    try:
        reader = csv.reader(io.StringIO(decoded, newline=''), dialect=dialect, strict=True)
        headers = [h.strip() for h in next(reader, [])]
        if not headers or not any(headers):
            raise ValueError('Il file è vuoto.')
        if len(headers) > MAX_CSV_COLUMNS:
            raise ValueError('Il file supera il limite di 100 colonne.')
        if any(not h for h in headers):
            raise ValueError('Ogni colonna deve avere un’intestazione.')
        if len({h.casefold() for h in headers}) != len(headers):
            raise ValueError('Le intestazioni delle colonne devono essere diverse.')
        rows = []
        for row in reader:
            rows.append(row)
            if len(rows) > MAX_CSV_ROWS:
                raise ValueError('Il file supera il limite di 2.000 righe. Dividilo in più file.')
        if not any(any(cell.strip() for cell in row) for row in rows):
            raise ValueError('Il file contiene solo le intestazioni o righe vuote.')
    except csv.Error as exc:
        raise ValueError('CSV non leggibile: controlla separatori, virgolette e dimensioni dei campi.') from exc
    return headers, rows, dialect.delimiter


def _guess_mapping(source_headers):
    aliases = {
        'p.iva': 'partita_iva', 'piva': 'partita_iva', 'p_iva': 'partita_iva',
        'vat': 'partita_iva', 'vat_number': 'partita_iva',
        'ragione sociale': 'ragione_sociale', 'nome': 'ragione_sociale',
        'azienda': 'ragione_sociale', 'denominazione': 'ragione_sociale',
        'cf': 'codice_fiscale', 'codice fiscale': 'codice_fiscale',
        'city': 'citta', 'città': 'citta', 'address': 'indirizzo', 'via': 'indirizzo',
        'country': 'nazione', 'zip': 'cap', 'postal': 'cap', 'sdi': 'codice_sdi',
        'industry': 'settore', 'sector': 'settore', 'type': 'tipo',
        'status': 'stato', 'state': 'stato',
    }
    mapping, used = {}, set()
    for header in source_headers:
        key = header.strip().lower()
        target = key if key in CSV_HEADERS else key.replace(' ', '_')
        if target not in CSV_HEADERS:
            target = aliases.get(key, '')
        mapping[header] = target if target not in used else ''
        if target:
            used.add(target)
    return mapping


def _mapping_context(payload, mapping=None):
    return {
        'step': 2, 'filename': payload['filename'],
        'source_headers': payload['headers'], 'row_count': len(payload['rows']),
        'preview_rows': payload['rows'][:5],
        'mapping': mapping if mapping is not None else payload.get('mapping', _guess_mapping(payload['headers'])),
        'upload_token': payload['token'],
    }


def _preview_context(request, payload, plan):
    action = request.GET.get('azione', '')
    if action not in {'create', 'update', 'unchanged', 'error'}:
        action = ''
    rows = [row for row in plan['rows'] if not action or row['action'] == action]
    page = Paginator(rows, 50).get_page(request.GET.get('page'))
    stale = plan['fingerprint'] != payload.get('fingerprint')
    return {
        'step': 3, 'filename': payload['filename'], 'row_count': len(payload['rows']), 'plan': plan,
        'preview_rows_plan': page.object_list, 'preview_page': page,
        'preview_action': action, 'preview_stale': stale,
        'upload_token': payload['token'], 'preview_token': payload.get('preview_token', ''),
        'can_import': not stale and bool(plan['counts']['create'] + plan['counts']['update']),
        'import_count': plan['counts']['create'] + plan['counts']['update'],
    }


def _store_preview(request, payload, mapping, plan):
    payload = {**payload, 'mapping': mapping, 'fingerprint': plan['fingerprint'],
               'preview_token': str(uuid.uuid4())}
    request.session[SESSION_KEY] = payload
    return payload


class _AnteprimaCambiata(Exception):
    pass


@require_perm('clienti.importa')
def import_csv(request):
    """Caricamento, corrispondenze, anteprima e conferma atomica con ricevuta."""
    from .importazione import esegui_piano, prepara_import

    base_ctx = {'active_nav': 'clienti', 'csv_headers': CSV_HEADERS,
                'target_labels': TARGET_FIELD_LABELS}

    def display(extra=None):
        return render(request, 'clienti/import_csv.html', {**base_ctx, **(extra or {})})

    def result_redirect(receipt):
        return HttpResponseRedirect(f'{reverse("clienti:import_csv")}?esito={receipt.pk}')

    if request.method == 'GET' and request.GET.get('esempio') == '1':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="esempio-clienti.csv"'
        response.write('\ufeff')
        writer = csv.writer(response)
        writer.writerow(CSV_HEADERS)
        writer.writerow(['Azienda di esempio', 'azienda', 'Consulenza', 'IT12345678901',
                         '', '', '', 'Via Roma 1', '20100', 'Milano', 'MI', 'IT',
                         'prospect', 'Dati dimostrativi: sostituire prima di importare'])
        return response

    if request.method == 'GET' and request.GET.get('esito'):
        try:
            token = uuid.UUID(request.GET['esito'])
        except ValueError:
            raise Http404
        receipt = get_object_or_404(EsitoImportazioneClienti, pk=token, autore=request.user)
        return display({'done': True, **receipt.riepilogo})

    step = request.POST.get('step', '')
    if request.method == 'POST' and step == '3':
        # La ricevuta resta valida anche dopo la pulizia/scadenza della sessione.
        try:
            token = uuid.UUID(request.POST.get('upload_token', ''))
        except ValueError:
            token = None
        receipt = EsitoImportazioneClienti.objects.filter(pk=token, autore=request.user).first() if token else None
        if receipt:
            return result_redirect(receipt)

    if request.method == 'POST' and not step:
        # Un nuovo caricamento invalida il precedente anche se il file non è valido.
        request.session.pop(SESSION_KEY, None)
        upload = request.FILES.get('file')
        if not upload:
            messages.error(request, 'Seleziona un file CSV.')
            return display()
        try:
            headers, rows, delimiter = _csv_decode_and_parse(upload.read(MAX_CSV_BYTES + 1))
        except ValueError as exc:
            messages.error(request, str(exc))
            return display()
        payload = {'headers': headers, 'rows': rows, 'delimiter': delimiter,
                   'filename': upload.name[:255], 'token': str(uuid.uuid4()),
                   'uploaded_at': time.time()}
        request.session[SESSION_KEY] = payload
        return display(_mapping_context(payload))

    is_preview = request.method == 'GET' and request.GET.get('fase') == 'anteprima'
    if request.method == 'POST' or is_preview:
        payload = request.session.get(SESSION_KEY)
        if not payload or time.time() - payload.get('uploaded_at', 0) > IMPORT_TTL_SECONDS:
            request.session.pop(SESSION_KEY, None)
            messages.error(request, 'Il caricamento è scaduto. Seleziona di nuovo il file CSV.')
            return display()
        if request.method == 'POST' and request.POST.get('upload_token') != payload['token']:
            messages.error(request, 'È stato caricato un altro file. Controlla il caricamento corrente prima di continuare.')
            return display(_mapping_context(payload))
        if step == 'mapping':
            # Tornare alle corrispondenze invalida la vecchia conferma.
            payload = {k: v for k, v in payload.items() if k not in {'fingerprint', 'preview_token'}}
            request.session[SESSION_KEY] = payload
            return display(_mapping_context(payload))
        if step == '2':
            mapping = {h: request.POST.get(f'map__{h}', '').strip() for h in payload['headers']}
            targets = [t for t in mapping.values() if t]
            missing = {'ragione_sociale', 'partita_iva'} - set(targets)
            duplicate = len(targets) != len(set(targets))
            invalid = set(targets) - set(CSV_HEADERS)
            if missing or duplicate or invalid:
                payload.pop('preview_token', None)
                payload.pop('fingerprint', None)
                request.session[SESSION_KEY] = payload
                if missing:
                    messages.error(request, 'Collega le colonne Ragione sociale e Partita IVA.')
                if duplicate:
                    messages.error(request, 'Ogni campo cliente può essere collegato a una sola colonna.')
                if invalid:
                    messages.error(request, 'Scegli i campi cliente tra quelli disponibili.')
                return display(_mapping_context(payload, mapping))
            plan = prepara_import(payload['headers'], payload['rows'], mapping)
            payload = _store_preview(request, payload, mapping, plan)
            return display(_preview_context(request, payload, plan))
        if is_preview and payload.get('preview_token'):
            plan = prepara_import(payload['headers'], payload['rows'], payload['mapping'])
            if plan['fingerprint'] != payload['fingerprint']:
                messages.warning(request, 'I dati sono cambiati. Torna alle corrispondenze e controlla una nuova anteprima.')
            return display(_preview_context(request, payload, plan))
        if step == '3' and payload.get('preview_token') and request.POST.get('preview_token') == payload['preview_token']:
            try:
                with transaction.atomic():
                    receipt, created = EsitoImportazioneClienti.objects.get_or_create(
                        pk=payload['token'], defaults={'autore': request.user, 'fingerprint': payload['fingerprint']},
                    )
                    if receipt.autore_id != request.user.pk:
                        raise Http404
                    if created:
                        plan = prepara_import(payload['headers'], payload['rows'], payload['mapping'], lock=True)
                        if plan['fingerprint'] != payload['fingerprint'] or not (plan['counts']['create'] + plan['counts']['update']):
                            raise _AnteprimaCambiata
                        counts = esegui_piano(plan)
                        errors = [[r['line'], r['error']] for r in plan['rows'] if r['action'] == 'error']
                        receipt.riepilogo = {
                            'filename': payload['filename'], 'created': counts['create'],
                            'updated': counts['update'], 'unchanged': counts['unchanged'],
                            'skipped': counts['error'], 'errors': errors[:50],
                            'error_overflow': max(0, len(errors) - 50),
                        }
                        receipt.save(update_fields=['riepilogo'])
                        audit_log(Azione.IMPORT, target_type='Cliente', target_id=str(receipt.pk),
                                  target_label=payload['filename'], request=request,
                                  meta={key: receipt.riepilogo[key] for key in ('created', 'updated', 'unchanged', 'skipped')})
            except (_AnteprimaCambiata, IntegrityError):
                plan = prepara_import(payload['headers'], payload['rows'], payload['mapping'])
                payload = _store_preview(request, payload, payload['mapping'], plan)
                messages.warning(request, 'I dati sono cambiati dopo l’anteprima. Nessuna modifica salvata: controlla il riepilogo aggiornato e conferma di nuovo.')
                return display(_preview_context(request, payload, plan))
            request.session.pop(SESSION_KEY, None)
            return result_redirect(receipt)
        messages.error(request, 'Controlla di nuovo le corrispondenze per generare un’anteprima valida.')
        return display(_mapping_context(payload))

    # GET è read-only: Chrome può precaricare questo link al passaggio del mouse.
    # Il payload viene sostituito solo da un nuovo caricamento esplicito.
    return display()


_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def _csv_safe(value):
    """Neutralizza la formula injection (Excel/LibreOffice) anteponendo un
    apice alle celle che iniziano con caratteri interpretati come formula."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


@require_perm('clienti.esporta')
def export_csv(request):
    """Export CSV dei clienti che matchano gli stessi filtri della list view."""
    qs = filtra_clienti(Cliente.objects.select_related('settore'), request.GET)

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="clienti.csv"'
    response.write('﻿')  # BOM UTF-8 per Excel
    writer = csv.writer(response, delimiter=',')
    writer.writerow(CSV_HEADERS)
    count = 0
    for c in qs:
        writer.writerow([_csv_safe(v) for v in (
            c.ragione_sociale, c.tipo, (c.settore.nome if c.settore else ''),
            c.partita_iva or '', c.codice_fiscale, c.codice_sdi, c.pec,
            c.indirizzo, c.cap, c.citta, c.provincia, c.nazione,
            c.stato, c.note.replace('\n', ' ').strip() if c.note else '',
        )])
        count += 1
    audit_log(
        Azione.EXPORT, target_type='Cliente',
        target_label=f'clienti.csv ({count} righe)', request=request,
        meta={'rows': count, 'filters': dict(request.GET.lists())},
    )
    return response
