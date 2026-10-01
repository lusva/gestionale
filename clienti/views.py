from __future__ import annotations

import csv
import io

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError, Q
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views.generic import (
    CreateView, DeleteView, DetailView, ListView, UpdateView,
)

from accounts.permissions import PermRequiredMixin, require_perm
from audit.models import Azione
from audit.utils import log as audit_log

from .forms import (
    SDI_RE, ClienteForm, ContattoForm, normalizza_partita_iva,
    valida_codice_fiscale,
)
from .models import Cliente, Contatto, Settore, StatoCliente, TipoCliente


class ClienteListView(LoginRequiredMixin, ListView):
    model = Cliente
    template_name = 'clienti/list.html'
    context_object_name = 'clienti'
    paginate_by = 8

    def get_queryset(self):
        qs = Cliente.objects.all().select_related('account_manager__profile', 'settore')
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(ragione_sociale__icontains=q)
                | Q(partita_iva__icontains=q)
                | Q(pec__icontains=q)
                | Q(contatti__email__icontains=q),
            ).distinct()
        stati = self.request.GET.getlist('stato')
        if stati:
            qs = qs.filter(stato__in=stati)
        settori = [s for s in self.request.GET.getlist('settore') if s]
        if settori:
            qs = qs.filter(settore__slug__in=settori)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'clienti'
        ctx['q'] = self.request.GET.get('q', '')
        ctx['stati_attivi'] = self.request.GET.getlist('stato')
        ctx['settori_attivi'] = self.request.GET.getlist('settore')
        from django.utils import timezone
        now = timezone.now()
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
        qs = Contatto.objects.select_related('cliente').order_by('-primary', 'cognome')
        q = self.request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(
                Q(nome__icontains=q) | Q(cognome__icontains=q) | Q(email__icontains=q)
                | Q(cliente__ragione_sociale__icontains=q),
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'contatti'
        ctx['q'] = self.request.GET.get('q', '')
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


def _csv_decode_and_parse(raw_bytes: bytes):
    """Decodifica con fallback e parsa il CSV restituendo (headers, rows, delimiter)."""
    try:
        decoded = raw_bytes.decode('utf-8-sig')
    except UnicodeDecodeError:
        decoded = raw_bytes.decode('latin-1')
    sample = decoded[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
        delimiter = dialect.delimiter
    except csv.Error:
        dialect = csv.excel
        delimiter = ','
    reader = csv.reader(io.StringIO(decoded), dialect=dialect)
    rows = list(reader)
    if not rows:
        return [], [], delimiter
    headers = [h.strip() for h in rows[0]]
    data_rows = rows[1:]
    return headers, data_rows, delimiter


def _guess_mapping(source_headers):
    """Prova a indovinare il mapping {source_header: target_field}.

    Match esatto case-insensitive con i target field ufficiali, oppure con
    alias comuni (es. 'p.iva' → 'partita_iva').
    """
    aliases = {
        'p.iva': 'partita_iva', 'piva': 'partita_iva', 'p_iva': 'partita_iva',
        'vat': 'partita_iva', 'vat_number': 'partita_iva',
        'ragione sociale': 'ragione_sociale', 'nome': 'ragione_sociale',
        'azienda': 'ragione_sociale', 'denominazione': 'ragione_sociale',
        'cf': 'codice_fiscale', 'codice fiscale': 'codice_fiscale',
        'city': 'citta', 'città': 'citta',
        'address': 'indirizzo', 'via': 'indirizzo',
        'country': 'nazione',
        'zip': 'cap', 'postal': 'cap',
        'sdi': 'codice_sdi',
        'industry': 'settore', 'sector': 'settore',
        'type': 'tipo',
        'status': 'stato', 'state': 'stato',
    }
    target_set = set(CSV_HEADERS)
    mapping = {}
    for h in source_headers:
        key = h.strip().lower()
        if key in target_set:
            mapping[h] = key
        elif key.replace(' ', '_') in target_set:
            mapping[h] = key.replace(' ', '_')
        elif key in aliases:
            mapping[h] = aliases[key]
        else:
            mapping[h] = ''  # da ignorare di default
    return mapping


SESSION_KEY = '_csv_import_payload'

def _settore_da_nome(nome: str):
    """Trova o crea un Settore per nome, con lookup per slug (evita
    IntegrityError su slug già presenti con nome diverso). Nome senza slug
    valido → None."""
    slug = slugify(nome)[:120]
    if not slug:
        return None
    settore = Settore.objects.filter(Q(slug=slug) | Q(nome__iexact=nome)).first()
    if settore is not None:
        return settore
    try:
        with transaction.atomic():
            return Settore.objects.create(nome=nome[:100], slug=slug)
    except IntegrityError:
        return Settore.objects.filter(Q(slug=slug) | Q(nome__iexact=nome)).first()


def _esegui_import(headers, rows, mapping):
    """Upsert dei clienti per partita IVA.

    In aggiornamento vengono toccati solo i campi mappati e non vuoti nella
    riga; in creazione i campi mancanti prendono i default del modello.
    Tutto l'import gira in una transazione; ogni riga in un savepoint così
    una riga invalida diventa una riga di errore senza annullare le altre.
    Ritorna ``(created, updated, skipped, errors)``.
    """
    source_index = {h: i for i, h in enumerate(headers)}
    target_to_idx = {}
    for src, tgt in mapping.items():
        if tgt and src in source_index and tgt not in target_to_idx:
            target_to_idx[tgt] = source_index[src]
    tipi = {v for v, _ in TipoCliente.choices}
    stati = {v for v, _ in StatoCliente.choices}
    created, updated, skipped = 0, 0, 0
    errors: list[tuple[int, str]] = []

    def get_mapped(row, target):
        """Ritorna il valore grezzo per il `target` applicando il mapping."""
        idx = target_to_idx.get(target)
        if idx is None or idx >= len(row):
            return ''
        return (row[idx] or '').strip()

    def scarta(lineno, msg):
        nonlocal skipped
        skipped += 1
        errors.append((lineno, msg))

    with transaction.atomic():
        for lineno, row in enumerate(rows, start=2):
            rs = get_mapped(row, 'ragione_sociale')
            piva_raw = get_mapped(row, 'partita_iva')
            nazione = get_mapped(row, 'nazione').upper()[:2]
            if not rs or not piva_raw:
                scarta(lineno, 'ragione_sociale o partita_iva mancante')
                continue
            piva, errore = normalizza_partita_iva(piva_raw, nazione or 'IT')
            if errore:
                scarta(lineno, f'P.IVA non valida: {piva_raw}')
                continue

            valori = {'ragione_sociale': rs[:200]}
            tipo = get_mapped(row, 'tipo').lower()
            if tipo:
                if tipo not in tipi:
                    scarta(lineno, f'Tipo non valido: {tipo}')
                    continue
                valori['tipo'] = tipo
            stato = get_mapped(row, 'stato').lower()
            if stato:
                if stato not in stati:
                    scarta(lineno, f'Stato non valido: {stato}')
                    continue
                valori['stato'] = stato
            cf = get_mapped(row, 'codice_fiscale').upper().replace(' ', '')
            if cf:
                if (nazione or 'IT') == 'IT' and not valida_codice_fiscale(cf):
                    scarta(lineno, f'Codice fiscale non valido: {cf}')
                    continue
                valori['codice_fiscale'] = cf
            sdi = get_mapped(row, 'codice_sdi').upper()
            if sdi:
                if not SDI_RE.match(sdi):
                    scarta(lineno, f'Codice SDI non valido: {sdi}')
                    continue
                valori['codice_sdi'] = sdi
            for campo, maxlen in (('pec', 254), ('indirizzo', 200), ('cap', 5),
                                  ('citta', 100), ('note', None)):
                v = get_mapped(row, campo)
                if v:
                    valori[campo] = v[:maxlen] if maxlen else v
            provincia = get_mapped(row, 'provincia').upper()[:2]
            if provincia:
                valori['provincia'] = provincia
            if nazione:
                valori['nazione'] = nazione
            settore_nome = get_mapped(row, 'settore')
            if settore_nome:
                settore_obj = _settore_da_nome(settore_nome)
                if settore_obj is not None:
                    valori['settore'] = settore_obj

            # Lookup tollerante: P.IVA italiane storiche possono essere
            # salvate senza il prefisso IT.
            candidati = [piva]
            if piva.startswith('IT'):
                candidati.append(piva[2:])
            esistenti = list(Cliente.objects.filter(partita_iva__in=candidati)[:2])
            if len(esistenti) > 1:
                scarta(lineno, f'P.IVA {piva} presente su più clienti')
                continue
            try:
                with transaction.atomic():
                    if esistenti:
                        cliente = esistenti[0]
                        for campo, v in valori.items():
                            setattr(cliente, campo, v)
                        cliente.save()
                        updated += 1
                    else:
                        valori.setdefault('tipo', TipoCliente.AZIENDA)
                        valori.setdefault('stato', StatoCliente.PROSPECT)
                        Cliente.objects.create(partita_iva=piva, **valori)
                        created += 1
            except IntegrityError as exc:
                scarta(lineno, f'Errore di salvataggio: {exc}')
    return created, updated, skipped, errors


@require_perm('clienti.importa')
def import_csv(request):
    """Import wizard a 2 step.

    Step 1 (GET o POST senza `step`) → upload file. Al POST con `file`:
        parse, salva in session e passa a step 2.
    Step 2 (POST con `step=2`) → conferma con mapping {source_col: target}.
        Esegue l'upsert e mostra il riepilogo.
    """
    base_ctx = {
        'active_nav': 'clienti',
        'csv_headers': CSV_HEADERS,
        'target_labels': TARGET_FIELD_LABELS,
    }

    step = request.POST.get('step', '')

    # ----------- STEP 1: upload -------------
    if request.method == 'POST' and step != '2':
        upload = request.FILES.get('file')
        if not upload:
            messages.error(request, 'Seleziona un file CSV.')
            return render(request, 'clienti/import_csv.html', base_ctx)
        try:
            raw = upload.read()
            headers, rows, delimiter = _csv_decode_and_parse(raw)
        except Exception as exc:
            messages.error(request, f'Impossibile leggere il file: {exc}')
            return render(request, 'clienti/import_csv.html', base_ctx)
        if not headers:
            messages.error(request, 'File vuoto o non leggibile.')
            return render(request, 'clienti/import_csv.html', base_ctx)

        # Salva l'intero payload in session per lo step 2 (preview fa un cap)
        request.session[SESSION_KEY] = {
            'headers': headers,
            'rows': rows,
            'delimiter': delimiter,
            'filename': upload.name,
        }
        mapping = _guess_mapping(headers)

        ctx = dict(base_ctx)
        ctx.update({
            'step': 2,
            'filename': upload.name,
            'source_headers': headers,
            'row_count': len(rows),
            'preview_rows': rows[:5],
            'mapping': mapping,
        })
        return render(request, 'clienti/import_csv.html', ctx)

    # ----------- STEP 2: conferma + import -------------
    if request.method == 'POST' and step == '2':
        payload = request.session.get(SESSION_KEY)
        if not payload:
            messages.error(request, 'Sessione scaduta, ricarica il file.')
            return render(request, 'clienti/import_csv.html', base_ctx)

        headers = payload['headers']
        rows = payload['rows']

        # Leggi mapping dal form
        mapping = {}
        for h in headers:
            target = (request.POST.get(f'map__{h}') or '').strip()
            if target in CSV_HEADERS:
                mapping[h] = target
            else:
                mapping[h] = ''

        # Valida che i 2 target obbligatori siano coperti
        mapped_targets = set(mapping.values()) - {''}
        missing = {'ragione_sociale', 'partita_iva'} - mapped_targets
        if missing:
            messages.error(
                request,
                f'Devi mappare i campi obbligatori: {", ".join(sorted(missing))}.',
            )
            ctx = dict(base_ctx)
            ctx.update({
                'step': 2,
                'filename': payload.get('filename'),
                'source_headers': headers,
                'row_count': len(rows),
                'preview_rows': rows[:5],
                'mapping': mapping,
            })
            return render(request, 'clienti/import_csv.html', ctx)

        # Esegui import
        try:
            created, updated, skipped, errors = _esegui_import(
                headers, rows, mapping,
            )
        finally:
            # Pulisci la session anche in caso di errore imprevisto.
            request.session.pop(SESSION_KEY, None)

        total = created + updated
        if total:
            messages.success(
                request,
                f'Import completato: {created} creati, {updated} aggiornati'
                + (f', {skipped} scartati' if skipped else '') + '.',
            )
        else:
            messages.warning(request, f'Nessuna riga importata ({skipped} scartate).')

        audit_log(
            Azione.IMPORT, target_type='Cliente',
            target_label=f'{payload.get("filename", "csv")}',
            request=request,
            meta={'created': created, 'updated': updated, 'skipped': skipped},
        )

        ctx = dict(base_ctx)
        ctx.update({
            'done': True,
            'created': created, 'updated': updated, 'skipped': skipped,
            'errors': errors[:50],
            'error_overflow': max(0, len(errors) - 50),
        })
        return render(request, 'clienti/import_csv.html', ctx)

    # GET → step 1 vuoto
    return render(request, 'clienti/import_csv.html', base_ctx)


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
    qs = Cliente.objects.all().select_related('settore')
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(
            Q(ragione_sociale__icontains=q)
            | Q(partita_iva__icontains=q)
            | Q(pec__icontains=q),
        )
    stati = request.GET.getlist('stato')
    if stati:
        qs = qs.filter(stato__in=stati)
    settori = [s for s in request.GET.getlist('settore') if s]
    if settori:
        qs = qs.filter(settore__slug__in=settori)

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
