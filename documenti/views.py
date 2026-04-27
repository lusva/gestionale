from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import (
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
)

from accounts.permissions import PermRequiredMixin

from .forms import (
    RigaFatturaFormSet,
    ScadenzeFatturaFormSet,
    TestataFatturaForm,
)
from .models import RigaDocumento, ScadenzaFattura, TestataFattura
from .utils import (
    calcola_valori,
    crea_pdf,
    firma_pdf_bytes,
    genera_scadenze_da_forma_pagamento,
)


# ---------------------------------------------------------------------------
# List (con filtri q / anno / pagata / tipo_documento)
# ---------------------------------------------------------------------------


class FatturaListView(LoginRequiredMixin, PermRequiredMixin, ListView):
    required_perm = 'documenti.vedi'
    model = TestataFattura
    template_name = 'documenti/fattura_list.html'
    context_object_name = 'fatture'
    paginate_by = 20

    def get_queryset(self):
        qs = TestataFattura.objects.select_related('cliente').order_by(
            '-anno', '-numero',
        )
        q = (self.request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(
                Q(numero__icontains=q)
                | Q(cliente__ragione_sociale__icontains=q)
                | Q(cliente__partita_iva__icontains=q)
                | Q(note__icontains=q),
            )
        anno = (self.request.GET.get('anno') or '').strip()
        if anno.isdigit():
            qs = qs.filter(anno=int(anno))
        pagata = self.request.GET.get('pagata')
        if pagata == 'si':
            qs = qs.filter(pagata=True)
        elif pagata == 'no':
            qs = qs.filter(pagata=False)
        tipo = (self.request.GET.get('tipo') or '').strip()
        if tipo:
            qs = qs.filter(tipo_documento=tipo)
        sezionale = (self.request.GET.get('sezionale') or '').strip()
        if sezionale:
            qs = qs.filter(sezionale=sezionale)
        sdi = (self.request.GET.get('sdi') or '').strip()
        if sdi:
            qs = qs.filter(sdi_stato=sdi)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'fatture'
        ctx['q'] = self.request.GET.get('q', '')
        ctx['anno'] = self.request.GET.get('anno', '')
        ctx['pagata'] = self.request.GET.get('pagata', '')
        ctx['tipo'] = self.request.GET.get('tipo', '')
        ctx['sezionale'] = self.request.GET.get('sezionale', '')
        ctx['tipi_choices'] = TestataFattura.TipoDocumento.choices
        # Anni disponibili (distinti) per il filtro
        ctx['anni_choices'] = list(
            TestataFattura.objects.values_list('anno', flat=True)
            .distinct().order_by('-anno'),
        )
        ctx['sezionali_choices'] = list(
            TestataFattura.objects.exclude(sezionale='')
            .values_list('sezionale', flat=True).distinct().order_by('sezionale'),
        )
        ctx['sdi'] = self.request.GET.get('sdi', '')
        ctx['sdi_choices'] = TestataFattura.SdiStato.choices
        # KPI rapidi
        agg = TestataFattura.objects.aggregate(
            tot=Sum('imponibile'),
        )
        ctx['kpi_totale'] = agg['tot'] or Decimal('0')
        ctx['kpi_count'] = TestataFattura.objects.count()
        ctx['kpi_da_pagare'] = TestataFattura.objects.filter(pagata=False).count()
        return ctx

    def get_template_names(self):
        if self.request.htmx and not self.request.htmx.boosted:
            return ['documenti/_fattura_table.html']
        return [self.template_name]


# ---------------------------------------------------------------------------
# Detail (testata + righe + scadenze)
# ---------------------------------------------------------------------------


class FatturaDetailView(LoginRequiredMixin, PermRequiredMixin, DetailView):
    required_perm = 'documenti.vedi'
    model = TestataFattura
    template_name = 'documenti/fattura_detail.html'
    context_object_name = 'fattura'

    def get_queryset(self):
        return TestataFattura.objects.select_related(
            'cliente', 'forme_pagamento', 'conto_corrente',
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        f: TestataFattura = self.object
        ctx['active_nav'] = 'fatture'
        ctx['righe'] = f.righe.select_related('articolo', 'iva').all()
        ctx['scadenze'] = f.scadenze.all()
        # Totali documento
        imponibile = sum((r.imponibile for r in ctx['righe']), Decimal('0'))
        imposta = sum((r.imposta for r in ctx['righe']), Decimal('0'))
        ctx['imponibile_calc'] = imponibile
        ctx['imposta_calc'] = imposta
        ctx['totale_calc'] = imponibile + imposta

        # Catena documenti collegati
        chain = []
        for o in f.ordini_origine.all():
            chain.append({'tipo': 'Ordine', 'doc': o,
                          'url_name': 'documenti:ordine_detail',
                          'verso': 'indietro'})
        for nc in f.nota_credito_collegata.all():
            chain.append({'tipo': 'Nota credito', 'doc': nc,
                          'url_name': 'documenti:nota_credito_detail',
                          'verso': 'avanti'})
        ctx['chain'] = chain
        ctx['messaggi_sdi'] = f.messaggi_sdi.all()
        return ctx


# ---------------------------------------------------------------------------
# Create / Update (testata + formset righe + formset scadenze, save atomico)
# ---------------------------------------------------------------------------


class _FatturaSaveMixin:
    """Logica condivisa per Create e Update: gestisce i formset inline."""

    template_name = 'documenti/fattura_form.html'

    def _build_formsets(self, instance, post_data=None):
        return {
            'righe_formset': RigaFatturaFormSet(
                post_data, instance=instance, prefix='righe',
            ),
            'scadenze_formset': ScadenzeFatturaFormSet(
                post_data, instance=instance, prefix='scadenze',
            ),
        }

    def _render(self, request, form, formsets, instance=None, is_create=True):
        ctx = {
            'active_nav': 'fatture',
            'is_create': is_create,
            'form': form,
            'object': instance,
            'fattura': instance,
            **formsets,
        }
        return render(request, self.template_name, ctx)


def _maybe_genera_scadenze_auto(fattura):
    """Auto-popola le scadenze fattura usando la forma di pagamento, se le
    scadenze sono ancora vuote e una forma è impostata."""
    fattura.refresh_from_db()
    if fattura.scadenze.exists():
        return
    if not fattura.forme_pagamento_id or not fattura.data_documento:
        return
    auto = genera_scadenze_da_forma_pagamento(fattura)
    for s in auto:
        ScadenzaFattura.objects.create(
            fattura=fattura, data=s['data'], importo=s['importo'],
        )


class FatturaCreateView(LoginRequiredMixin, PermRequiredMixin, _FatturaSaveMixin, TemplateView):
    required_perm = 'documenti.modifica'

    def get(self, request, *args, **kwargs):
        form = TestataFatturaForm()
        formsets = self._build_formsets(instance=TestataFattura())
        return self._render(request, form, formsets, is_create=True)

    def post(self, request, *args, **kwargs):
        form = TestataFatturaForm(request.POST)
        if not form.is_valid():
            formsets = self._build_formsets(instance=TestataFattura(), post_data=request.POST)
            return self._render(request, form, formsets, is_create=True)
        # Salvo la testata in transazione, poi i formset
        with transaction.atomic():
            fattura = form.save()
            formsets = self._build_formsets(instance=fattura, post_data=request.POST)
            righe_fs = formsets['righe_formset']
            scad_fs = formsets['scadenze_formset']
            if not (righe_fs.is_valid() and scad_fs.is_valid()):
                # Rollback: cancello la testata appena creata
                fattura.delete()
                return self._render(request, form, formsets, is_create=True)
            righe_fs.save()
            scad_fs.save()
            _maybe_genera_scadenze_auto(fattura)
        messages.success(
            request,
            f'Fattura {fattura.numero}/{fattura.anno} creata con successo.',
        )
        return redirect('documenti:fattura_detail', pk=fattura.pk)


class FatturaUpdateView(LoginRequiredMixin, PermRequiredMixin, _FatturaSaveMixin, TemplateView):
    required_perm = 'documenti.modifica'

    def _get_obj(self, pk):
        return get_object_or_404(TestataFattura, pk=pk)

    def get(self, request, pk, *args, **kwargs):
        fattura = self._get_obj(pk)
        form = TestataFatturaForm(instance=fattura)
        formsets = self._build_formsets(instance=fattura)
        return self._render(request, form, formsets, instance=fattura, is_create=False)

    def post(self, request, pk, *args, **kwargs):
        fattura = self._get_obj(pk)
        form = TestataFatturaForm(request.POST, instance=fattura)
        formsets = self._build_formsets(instance=fattura, post_data=request.POST)
        all_valid = (
            form.is_valid()
            and formsets['righe_formset'].is_valid()
            and formsets['scadenze_formset'].is_valid()
        )
        if not all_valid:
            return self._render(request, form, formsets, instance=fattura, is_create=False)
        with transaction.atomic():
            form.save()
            formsets['righe_formset'].save()
            formsets['scadenze_formset'].save()
            _maybe_genera_scadenze_auto(fattura)
        messages.success(request, 'Fattura aggiornata.')
        return redirect('documenti:fattura_detail', pk=fattura.pk)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class FatturaDeleteView(LoginRequiredMixin, PermRequiredMixin, DeleteView):
    required_perm = 'documenti.elimina'
    model = TestataFattura
    template_name = 'documenti/confirm_delete.html'
    success_url = reverse_lazy('documenti:fattura_list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'fatture'
        ctx['object_label'] = (
            f'Fattura {self.object.numero}/{self.object.anno} '
            f'— {self.object.cliente or ""}'
        )
        ctx['back_url'] = reverse('documenti:fattura_detail', args=[self.object.pk])
        return ctx


# ---------------------------------------------------------------------------
# Toggle "pagata" rapido (HTMX) + endpoint per riga vuota inline
# ---------------------------------------------------------------------------


def fattura_toggle_pagata(request, pk):
    """HTMX: toggle del flag ``pagata`` con doppia conferma via form POST."""
    if request.method != 'POST':
        return HttpResponseRedirect(reverse('documenti:fattura_detail', args=[pk]))
    from accounts.permissions import has_perm
    if not has_perm(request.user, 'documenti.modifica'):
        from django.http import HttpResponse
        return HttpResponse(status=403)
    fattura = get_object_or_404(TestataFattura, pk=pk)
    fattura.pagata = not fattura.pagata
    if fattura.pagata and not fattura.data_pagamento:
        from django.utils.timezone import now as _now
        fattura.data_pagamento = _now().date()
    elif not fattura.pagata:
        fattura.data_pagamento = None
    fattura.save(update_fields=['pagata', 'data_pagamento'])
    return redirect('documenti:fattura_detail', pk=pk)


def _generic_doc_pdf(request, model, pk, *, doc_label, doc_kind, counterparty='cliente',
                     show_iva=True, show_totals=True, has_stato=False):
    """Genera il PDF di un documento (offerta/ordine/ddt/nota credito)."""
    from accounts.permissions import has_perm
    from django.http import HttpResponse as _Hr
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not has_perm(request.user, 'documenti.vedi'):
        return _Hr(status=403)

    documento = get_object_or_404(
        model.objects.select_related(counterparty, 'forme_pagamento'),
        pk=pk,
    )
    from anagrafiche.models import AnagraficaAzienda
    azienda = AnagraficaAzienda.objects.first()
    totals = calcola_valori(documento) if show_totals else None
    context = {
        'documento': documento,
        'azienda': azienda,
        'doc_label': doc_label,
        'doc_kind': doc_kind,
        'counterparty': counterparty,
        'has_stato': has_stato,
        'show_iva': show_iva,
        'show_totals': show_totals,
        'righe': documento.righe.select_related('articolo', 'iva').all(),
        'totals': totals,
    }
    filename = f'{doc_kind}_{documento.numero}_{documento.anno}.pdf'
    return crea_pdf('documenti/pdf/documento.html', context, filename)


def offerta_pdf(request, pk):
    from .models import TestataOfferta
    return _generic_doc_pdf(
        request, TestataOfferta, pk,
        doc_label='Offerta', doc_kind='offerta', has_stato=True,
    )


def ordine_pdf(request, pk):
    from .models import TestataOrdine
    return _generic_doc_pdf(
        request, TestataOrdine, pk,
        doc_label='Ordine', doc_kind='ordine', has_stato=True,
    )


def ddt_pdf(request, pk):
    from .models import TestataDdt
    # I DDT non riportano i totali a fini fiscali → nascondiamo riepilogo IVA
    return _generic_doc_pdf(
        request, TestataDdt, pk,
        doc_label='DDT', doc_kind='ddt',
        show_iva=False, show_totals=False, has_stato=True,
    )


def nota_credito_pdf(request, pk):
    from .models import TestataNotaCredito
    return _generic_doc_pdf(
        request, TestataNotaCredito, pk,
        doc_label='Nota di credito', doc_kind='nota_credito',
    )


class FatturaAcquistoListView(LoginRequiredMixin, PermRequiredMixin, ListView):
    required_perm = 'documenti.vedi'
    template_name = 'documenti/fattura_acquisto_list.html'
    context_object_name = 'fatture'
    paginate_by = 20

    def get_queryset(self):
        from .models import TestataFatturaAcquisto
        qs = TestataFatturaAcquisto.objects.select_related('fornitore').order_by(
            '-anno', '-numero',
        )
        q = (self.request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(
                Q(numero__icontains=q)
                | Q(numero_fornitore__icontains=q)
                | Q(fornitore__ragione_sociale__icontains=q)
                | Q(fornitore__partita_iva__icontains=q),
            )
        anno = self.request.GET.get('anno', '').strip()
        if anno.isdigit():
            qs = qs.filter(anno=int(anno))
        stato = self.request.GET.get('stato', '').strip()
        if stato:
            qs = qs.filter(stato=stato)
        pagata = self.request.GET.get('pagata')
        if pagata == 'si':
            qs = qs.filter(pagata=True)
        elif pagata == 'no':
            qs = qs.filter(pagata=False)
        return qs

    def get_context_data(self, **kwargs):
        from .models import TestataFatturaAcquisto
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'fatture_acquisto'
        ctx['q'] = self.request.GET.get('q', '')
        ctx['anno'] = self.request.GET.get('anno', '')
        ctx['stato'] = self.request.GET.get('stato', '')
        ctx['pagata'] = self.request.GET.get('pagata', '')
        ctx['stato_choices'] = TestataFatturaAcquisto.StatoFattura.choices
        ctx['anni_choices'] = list(
            TestataFatturaAcquisto.objects.values_list('anno', flat=True)
            .distinct().order_by('-anno'),
        )
        return ctx


class FatturaAcquistoDetailView(LoginRequiredMixin, PermRequiredMixin, DetailView):
    required_perm = 'documenti.vedi'
    template_name = 'documenti/fattura_acquisto_detail.html'
    context_object_name = 'fattura'

    def get_queryset(self):
        from .models import TestataFatturaAcquisto
        return TestataFatturaAcquisto.objects.select_related('fornitore')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'fatture_acquisto'
        f = self.object
        ctx['righe'] = f.righe.select_related('articolo', 'aliquota_iva', 'categoria_costo').all()
        ctx['scadenze'] = f.scadenze.prefetch_related('pagamenti').all()
        return ctx


def fattura_acquisto_cambia_stato(request, pk):
    """POST: passa la fattura allo stato indicato (bozza/confermata/annullata)."""
    from accounts.permissions import has_perm
    from django.http import HttpResponse as _Hr
    from .models import TestataFatturaAcquisto
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not has_perm(request.user, 'documenti.modifica'):
        return _Hr(status=403)
    if request.method != 'POST':
        return redirect('documenti:fattura_acquisto_detail', pk=pk)

    fattura = get_object_or_404(TestataFatturaAcquisto, pk=pk)
    nuovo_stato = request.POST.get('stato', '').strip()
    valid = {c[0] for c in TestataFatturaAcquisto.StatoFattura.choices}
    if nuovo_stato not in valid:
        messages.error(request, 'Stato non valido.')
        return redirect('documenti:fattura_acquisto_detail', pk=pk)
    fattura.stato = nuovo_stato
    fattura.save(update_fields=['stato'])
    messages.success(request, f'Fattura {fattura.numero}/{fattura.anno} → {fattura.get_stato_display()}.')
    return redirect('documenti:fattura_acquisto_detail', pk=pk)


def pagamento_scadenza_acquisto_create(request, scadenza_id):
    """POST per registrare un pagamento su una scadenza acquisto."""
    from accounts.permissions import has_perm
    from django.http import HttpResponse as _Hr
    from decimal import Decimal, InvalidOperation
    from datetime import date as _date
    from .models import PagamentoScadenzaAcquisto, ScadenzaFatturaAcquisto
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not has_perm(request.user, 'documenti.modifica'):
        return _Hr(status=403)
    if request.method != 'POST':
        return _Hr(status=405)

    scadenza = get_object_or_404(ScadenzaFatturaAcquisto, pk=scadenza_id)

    raw_importo = request.POST.get('importo', '').replace(',', '.').strip()
    try:
        importo = Decimal(raw_importo) if raw_importo else scadenza.importo_residuo
    except InvalidOperation:
        messages.error(request, 'Importo non valido.')
        return redirect('documenti:fattura_acquisto_detail', pk=scadenza.fattura_id)
    if importo <= 0:
        messages.error(request, "L'importo del pagamento deve essere maggiore di zero.")
        return redirect('documenti:fattura_acquisto_detail', pk=scadenza.fattura_id)

    raw_data = request.POST.get('data_pagamento', '').strip()
    try:
        data_pag = _date.fromisoformat(raw_data) if raw_data else _date.today()
    except ValueError:
        data_pag = _date.today()

    PagamentoScadenzaAcquisto.objects.create(
        scadenza=scadenza,
        data_pagamento=data_pag,
        importo=importo,
        modalita_pagamento=request.POST.get('modalita_pagamento') or scadenza.modalita_pagamento,
        note=request.POST.get('note', '') or None,
    )
    messages.success(request, f'Pagamento di {importo} € registrato.')
    return redirect('documenti:fattura_acquisto_detail', pk=scadenza.fattura_id)


def fattura_acquisto_import_xml(request):
    """UI + handler per importare una fattura elettronica di acquisto via XML."""
    from accounts.permissions import has_perm
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not has_perm(request.user, 'documenti.modifica'):
        messages.error(request, 'Non hai i permessi per importare fatture.')
        return redirect('documenti:fattura_list')

    ctx = {'active_nav': 'fatture_acquisto'}

    if request.method == 'POST':
        from .xml_import import import_fattura_from_xml

        upload = request.FILES.get('xml_file')
        if upload is None:
            messages.error(request, "Seleziona un file XML.")
            return render(request, 'documenti/fattura_acquisto_import.html', ctx)
        if upload.size > 10 * 1024 * 1024:
            messages.error(request, 'File troppo grande (max 10 MB).')
            return render(request, 'documenti/fattura_acquisto_import.html', ctx)
        if upload.name.lower().endswith('.p7m'):
            messages.error(
                request,
                "I file firmati .p7m non sono supportati — estrai l'XML prima di caricarlo.",
            )
            return render(request, 'documenti/fattura_acquisto_import.html', ctx)

        try:
            data = upload.read()
            result = import_fattura_from_xml(data, filename=upload.name)
        except ValueError as exc:
            messages.error(request, f'Import fallito: {exc}')
            return render(request, 'documenti/fattura_acquisto_import.html', ctx)

        ctx.update({'result': result, 'filename': upload.name})
        return render(request, 'documenti/fattura_acquisto_import.html', ctx)

    return render(request, 'documenti/fattura_acquisto_import.html', ctx)


def fattura_invia_email(request, pk):
    """Invia la fattura per email al cliente, con PDF e XML allegati.

    L'indirizzo destinatario può essere passato esplicitamente nel form
    POST (campo ``to``); se omesso, viene letto dalla PEC del cliente o,
    in alternativa, dal contatto principale.
    """
    from accounts.permissions import has_perm
    from django.core.mail import EmailMessage
    from django.conf import settings as django_settings
    from django.http import HttpResponse as _Hr

    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not has_perm(request.user, 'documenti.modifica'):
        return _Hr(status=403)
    if request.method != 'POST':
        return redirect('documenti:fattura_detail', pk=pk)

    fattura = get_object_or_404(
        TestataFattura.objects.select_related('cliente'), pk=pk,
    )

    # Risolvi indirizzo destinatario
    to_addr = (request.POST.get('to') or '').strip()
    if not to_addr and fattura.cliente:
        to_addr = (fattura.cliente.pec or '').strip()
    if not to_addr and fattura.cliente:
        primary = fattura.cliente.contatti.filter(primary=True).first()
        if primary and primary.email:
            to_addr = primary.email
    if not to_addr:
        messages.error(
            request,
            'Nessun indirizzo email disponibile (PEC cliente o contatto '
            'principale assenti).',
        )
        return redirect('documenti:fattura_detail', pk=pk)

    # Genero PDF in memoria
    from io import BytesIO
    from django.template.loader import render_to_string
    from xhtml2pdf import pisa
    from anagrafiche.models import AnagraficaAzienda
    from .utils import _link_callback

    azienda = AnagraficaAzienda.objects.first()
    totals = calcola_valori(fattura)
    pdf_ctx = {
        'fattura': fattura, 'azienda': azienda,
        'righe': fattura.righe.select_related('articolo', 'iva').all(),
        'scadenze': fattura.scadenze.all(),
        'totals': totals,
    }
    html = render_to_string('documenti/pdf/fattura.html', pdf_ctx)
    pdf_buf = BytesIO()
    pisa.CreatePDF(src=html, dest=pdf_buf, encoding='utf-8', link_callback=_link_callback)
    pdf_bytes = pdf_buf.getvalue()

    # XML opzionale: lo allego solo se il cliente ha SDI/PEC configurati
    xml_bytes = None
    if fattura.cliente and fattura.cliente.fatturazione_elettronica_id:
        from .fatt_el import FatturaElettronicaError, build_fattura_xml
        try:
            xml_bytes = build_fattura_xml(fattura)
        except FatturaElettronicaError:
            xml_bytes = None

    # Componi messaggio
    soggetto = (
        request.POST.get('subject')
        or f'Fattura {fattura.numero}/{fattura.anno}'
        + (f' — {azienda.ragione_sociale}' if azienda and azienda.ragione_sociale else '')
    )
    body = (
        request.POST.get('body')
        or f'In allegato la fattura {fattura.numero}/{fattura.anno} '
        f'del {fattura.data_documento.strftime("%d/%m/%Y")}.\n\n'
        + (
            f'Cordiali saluti,\n{azienda.ragione_sociale}'
            if azienda and azienda.ragione_sociale else ''
        )
    )
    msg = EmailMessage(
        subject=soggetto,
        body=body,
        from_email=django_settings.DEFAULT_FROM_EMAIL,
        to=[to_addr],
    )
    msg.attach(
        f'fattura_{fattura.numero}_{fattura.anno}.pdf',
        pdf_bytes,
        'application/pdf',
    )
    if xml_bytes:
        # Nome SDI-conforme: IT<piva>_<progressivo>.xml
        piva = ((azienda.partita_iva or '00000000000') if azienda else '').upper().replace('IT', '')
        msg.attach(
            f'IT{piva}_{fattura.numero:05d}{fattura.anno}.xml',
            xml_bytes,
            'application/xml',
        )

    try:
        msg.send(fail_silently=False)
    except Exception as exc:
        messages.error(request, f'Invio email fallito: {exc}')
        return redirect('documenti:fattura_detail', pk=pk)

    messages.success(request, f'Email inviata a {to_addr}.')
    return redirect('documenti:fattura_detail', pk=pk)


def fattura_invia_sdi(request, pk):
    """Costruisce l'XML FatturaPA e lo invia al SDI tramite il backend
    configurato sull'AnagraficaAzienda. Registra l'invio in MessaggioSdi."""
    from accounts.permissions import has_perm
    from django.http import HttpResponse as _Hr

    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not has_perm(request.user, 'documenti.modifica'):
        return _Hr(status=403)
    if request.method != 'POST':
        return redirect('documenti:fattura_detail', pk=pk)

    fattura = get_object_or_404(TestataFattura, pk=pk)

    if fattura.sdi_stato in (
        TestataFattura.SdiStato.INVIATA,
        TestataFattura.SdiStato.ACCETTATA,
        TestataFattura.SdiStato.RICEVUTA_SDI,
    ):
        messages.warning(request, 'La fattura risulta già inviata al SDI.')
        return redirect('documenti:fattura_detail', pk=pk)

    from .fatt_el import FatturaElettronicaError, build_fattura_xml
    from .sdi import get_backend
    from anagrafiche.models import AnagraficaAzienda
    from .models import MessaggioSdi

    backend = get_backend()
    if backend is None:
        messages.error(
            request,
            "Backend SDI non configurato sull'anagrafica azienda. "
            "Imposta sdi_provider e (se PEC) le credenziali SMTP.",
        )
        return redirect('documenti:fattura_detail', pk=pk)

    try:
        xml_bytes = build_fattura_xml(fattura)
    except FatturaElettronicaError as exc:
        messages.error(request, f'Impossibile generare la FatturaPA: {exc}')
        return redirect('documenti:fattura_detail', pk=pk)

    az = AnagraficaAzienda.objects.first()
    piva = ((az.partita_iva or '00000000000') if az else '').upper().replace('IT', '')
    filename = f'IT{piva}_{fattura.numero:05d}{fattura.anno}.xml'

    result = backend.send_xml(fattura, xml_bytes, filename)
    from django.utils.timezone import now as _now
    if result.success:
        TestataFattura.objects.filter(pk=fattura.pk).update(
            sdi_stato=TestataFattura.SdiStato.INVIATA,
            sdi_data_invio=_now(),
            sdi_id_trasmissione=result.id_trasmissione or '',
            sdi_ultimo_messaggio=result.descrizione,
        )
        MessaggioSdi.objects.create(
            fattura=fattura, tipo='invio',
            id_trasmissione=result.id_trasmissione or '',
            descrizione=result.descrizione,
            payload=xml_bytes.decode('utf-8', errors='replace')[:8000],
            direzione='out',
        )
        messages.success(
            request,
            f'Fattura inviata al SDI ({result.descrizione or "OK"}).',
        )
    else:
        TestataFattura.objects.filter(pk=fattura.pk).update(
            sdi_ultimo_messaggio=result.descrizione,
        )
        messages.error(request, f'Invio fallito: {result.descrizione}')

    return redirect('documenti:fattura_detail', pk=pk)


def fattura_xml_elettronica(request, pk):
    """Assembla e scarica la FatturaPA XML v1.2 per una fattura cliente."""
    from accounts.permissions import has_perm
    from django.http import HttpResponse as _Hr
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not has_perm(request.user, 'documenti.vedi'):
        return _Hr(status=403)
    from .fatt_el import FatturaElettronicaError, build_fattura_xml
    fattura = get_object_or_404(TestataFattura, pk=pk)
    try:
        xml_bytes = build_fattura_xml(fattura)
    except FatturaElettronicaError as exc:
        messages.error(request, f'FatturaPA non generabile: {exc}')
        return redirect('documenti:fattura_detail', pk=pk)
    # Nome file conforme SDI: IT<PIVA>_<progressivo>.xml
    from anagrafiche.models import AnagraficaAzienda
    az = AnagraficaAzienda.objects.first()
    piva = (az.partita_iva or '00000000000').upper().replace('IT', '')
    filename = f'IT{piva}_{fattura.numero:05d}{fattura.anno}.xml'
    response = _Hr(xml_bytes, content_type='application/xml; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def fatture_zip(request):
    """Scarica un .zip con i PDF di tutte le fatture che matchano i filtri.

    Riusa la stessa logica di filtro della list view: si passano gli stessi
    parametri (q, anno, pagata, tipo) della query string e si ottiene un zip
    con un PDF per ogni fattura. Limite di sicurezza: 200 fatture per zip.
    """
    from accounts.permissions import has_perm
    from django.http import HttpResponse as _Hr
    from io import BytesIO
    import zipfile
    from django.template.loader import render_to_string
    from xhtml2pdf import pisa
    from anagrafiche.models import AnagraficaAzienda
    from .utils import _link_callback

    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not has_perm(request.user, 'documenti.vedi'):
        return _Hr(status=403)

    qs = TestataFattura.objects.select_related('cliente').order_by(
        '-anno', '-numero',
    )
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(
            Q(numero__icontains=q)
            | Q(cliente__ragione_sociale__icontains=q)
            | Q(cliente__partita_iva__icontains=q),
        )
    anno = (request.GET.get('anno') or '').strip()
    if anno.isdigit():
        qs = qs.filter(anno=int(anno))
    pagata = request.GET.get('pagata')
    if pagata == 'si':
        qs = qs.filter(pagata=True)
    elif pagata == 'no':
        qs = qs.filter(pagata=False)
    tipo = (request.GET.get('tipo') or '').strip()
    if tipo:
        qs = qs.filter(tipo_documento=tipo)

    qs = qs[:200]
    if not qs:
        messages.warning(request, 'Nessuna fattura trovata con i filtri attuali.')
        return redirect('documenti:fattura_list')

    azienda = AnagraficaAzienda.objects.first()
    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in qs:
            totals = calcola_valori(f)
            ctx = {
                'fattura': f, 'azienda': azienda,
                'righe': f.righe.select_related('articolo', 'iva').all(),
                'scadenze': f.scadenze.all(),
                'totals': totals,
            }
            html = render_to_string('documenti/pdf/fattura.html', ctx)
            pdf_buf = BytesIO()
            pisa.CreatePDF(
                src=html, dest=pdf_buf,
                encoding='utf-8', link_callback=_link_callback,
            )
            zf.writestr(
                f'fattura_{f.numero}_{f.anno}.pdf',
                pdf_buf.getvalue(),
            )

    from django.utils.timezone import now as _now
    response = _Hr(buf.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = (
        f'attachment; filename="fatture_{_now().date().isoformat()}.zip"'
    )
    return response


def fattura_pdf_firmato(request, pk):
    """Versione PAdES-firmata del PDF fattura.

    Riusa il rendering di ``fattura_pdf`` e poi applica la firma con
    pyHanko leggendo il certificato P12 dall'AnagraficaAzienda. Se il
    certificato non è configurato, redirige con un messaggio di errore.
    """
    from accounts.permissions import has_perm
    from django.http import HttpResponse as _Hr
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not has_perm(request.user, 'documenti.vedi'):
        return _Hr(status=403)

    fattura = get_object_or_404(
        TestataFattura.objects.select_related(
            'cliente', 'forme_pagamento', 'conto_corrente',
        ),
        pk=pk,
    )
    from anagrafiche.models import AnagraficaAzienda
    azienda = AnagraficaAzienda.objects.first()
    if azienda is None or not azienda.certificato_p12 or not azienda.certificato_password:
        messages.error(
            request,
            'Certificato di firma non configurato sull\'anagrafica azienda. '
            'Carica il file .p12 e imposta la password per generare il PDF firmato.',
        )
        return redirect('documenti:fattura_detail', pk=pk)

    totals = calcola_valori(fattura)
    context = {
        'fattura': fattura,
        'azienda': azienda,
        'righe': fattura.righe.select_related('articolo', 'iva').all(),
        'scadenze': fattura.scadenze.all(),
        'totals': totals,
    }
    # Render PDF in memoria (riuso lo stesso template usato dalla view non firmata).
    from io import BytesIO
    from django.template.loader import render_to_string
    from xhtml2pdf import pisa
    from .utils import _link_callback

    html = render_to_string('documenti/pdf/fattura.html', context)
    buffer = BytesIO()
    pisa.CreatePDF(
        src=html, dest=buffer, encoding='utf-8', link_callback=_link_callback,
    )
    pdf_bytes = buffer.getvalue()

    try:
        signed = firma_pdf_bytes(pdf_bytes)
    except Exception as exc:
        messages.error(request, f'Errore durante la firma: {exc}')
        return redirect('documenti:fattura_detail', pk=pk)
    if signed is None:
        messages.error(request, 'Firma non disponibile (certificato mancante).')
        return redirect('documenti:fattura_detail', pk=pk)

    filename = f'fattura_{fattura.numero}_{fattura.anno}_firmata.pdf'
    response = _Hr(signed, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


def fattura_pdf(request, pk):
    """Genera il PDF di una fattura cliente."""
    from accounts.permissions import has_perm
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not has_perm(request.user, 'documenti.vedi'):
        from django.http import HttpResponse as _Hr
        return _Hr(status=403)
    fattura = get_object_or_404(
        TestataFattura.objects.select_related(
            'cliente', 'forme_pagamento', 'conto_corrente',
        ),
        pk=pk,
    )
    from anagrafiche.models import AnagraficaAzienda
    azienda = AnagraficaAzienda.objects.first()
    totals = calcola_valori(fattura)
    context = {
        'fattura': fattura,
        'azienda': azienda,
        'righe': fattura.righe.select_related('articolo', 'iva').all(),
        'scadenze': fattura.scadenze.all(),
        'totals': totals,
    }
    filename = f'fattura_{fattura.numero}_{fattura.anno}.pdf'
    return crea_pdf('documenti/pdf/fattura.html', context, filename)


def fattura_riga_form_empty(request):
    """Restituisce un form-row vuoto per il formset righe (HTMX append)."""
    index = int(request.GET.get('index', 0))
    formset = RigaFatturaFormSet(prefix='righe')
    # Forziamo total_forms al valore richiesto per evitare collisioni di name=
    formset.management_form.initial['TOTAL_FORMS'] = index + 1
    empty_form = formset.empty_form
    # Sostituisco il prefisso __prefix__ con l'indice reale
    html = empty_form.as_p().replace('__prefix__', str(index))
    return render(
        request,
        'documenti/_riga_row.html',
        {'form': empty_form, 'index': index},
    )
