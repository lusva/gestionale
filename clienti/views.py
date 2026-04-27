from __future__ import annotations

import csv
import io

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views.generic import (
    CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView, View,
)

from accounts.permissions import PermRequiredMixin, require_perm
from audit.models import Azione
from audit.utils import log as audit_log

from .forms import ClienteForm, ContattoForm
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


class ContattoCreateView(LoginRequiredMixin, CreateView):
    model = Contatto
    form_class = ContattoForm
    template_name = 'clienti/contatto_form.html'

    def dispatch(self, request, *args, **kwargs):
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
        source_index = {h: i for i, h in enumerate(headers)}
        tipi = {v for v, _ in TipoCliente.choices}
        stati = {v for v, _ in StatoCliente.choices}
        created, updated, skipped = 0, 0, 0
        errors: list[tuple[int, str]] = []

        def get_mapped(row, target):
            """Ritorna il valore grezzo per il `target` applicando il mapping."""
            for src, tgt in mapping.items():
                if tgt == target:
                    idx = source_index.get(src)
                    if idx is None or idx >= len(row):
                        return ''
                    return (row[idx] or '').strip()
            return ''

        for lineno, row in enumerate(rows, start=2):
            rs = get_mapped(row, 'ragione_sociale')
            piva_raw = get_mapped(row, 'partita_iva').upper().replace(' ', '')
            if not rs or not piva_raw:
                skipped += 1
                errors.append((lineno, 'ragione_sociale o partita_iva mancante'))
                continue
            piva = piva_raw[2:] if piva_raw.startswith('IT') else piva_raw
            if not piva.isdigit() or len(piva) != 11:
                skipped += 1
                errors.append((lineno, f'P.IVA non valida: {piva_raw}'))
                continue

            settore_nome = get_mapped(row, 'settore')
            settore_obj = None
            if settore_nome:
                settore_obj, _ = Settore.objects.get_or_create(
                    nome=settore_nome,
                    defaults={'slug': slugify(settore_nome)[:120]},
                )

            tipo = (get_mapped(row, 'tipo') or TipoCliente.AZIENDA).lower()
            if tipo not in tipi:
                tipo = TipoCliente.AZIENDA
            stato = (get_mapped(row, 'stato') or StatoCliente.PROSPECT).lower()
            if stato not in stati:
                stato = StatoCliente.PROSPECT

            defaults = {
                'ragione_sociale': rs,
                'tipo': tipo,
                'settore': settore_obj,
                'codice_fiscale': get_mapped(row, 'codice_fiscale'),
                'codice_sdi': get_mapped(row, 'codice_sdi'),
                'pec': get_mapped(row, 'pec'),
                'indirizzo': get_mapped(row, 'indirizzo'),
                'cap': get_mapped(row, 'cap'),
                'citta': get_mapped(row, 'citta'),
                'provincia': get_mapped(row, 'provincia').upper()[:2],
                'nazione': (get_mapped(row, 'nazione') or 'IT').upper()[:2] or 'IT',
                'stato': stato,
                'note': get_mapped(row, 'note'),
            }
            _, was_created = Cliente.objects.update_or_create(
                partita_iva=f'IT{piva}', defaults=defaults,
            )
            if was_created:
                created += 1
            else:
                updated += 1

        # Pulisci la session
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
        writer.writerow([
            c.ragione_sociale, c.tipo, (c.settore.nome if c.settore else ''),
            c.partita_iva, c.codice_fiscale, c.codice_sdi, c.pec,
            c.indirizzo, c.cap, c.citta, c.provincia, c.nazione,
            c.stato, c.note.replace('\n', ' ').strip() if c.note else '',
        ])
        count += 1
    audit_log(
        Azione.EXPORT, target_type='Cliente',
        target_label=f'clienti.csv ({count} righe)', request=request,
        meta={'rows': count, 'filters': dict(request.GET.lists())},
    )
    return response
