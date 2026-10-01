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
from .models import TestataFattura
from .servizi import base_scadenze, sincronizza_scadenze
from .utils import (
    calcola_valori,
    crea_pdf,
    firma_pdf_bytes,
    render_pdf_bytes,
    resolve_conto_corrente,
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
        # Totali documento: gli stessi di PDF e XML (sconti, spese, cassa)
        totals = calcola_valori(f)
        ctx['totals'] = totals
        ctx['imponibile_calc'] = totals['imponibile']
        ctx['imposta_calc'] = totals['imposta_totale']
        ctx['totale_calc'] = totals['totale']

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


def _maybe_genera_scadenze_auto(fattura, scadenze_formset=None, prima=None):
    """Sincronizza le scadenze con la forma di pagamento.

    - Se la fattura non ha scadenze e c'è una forma di pagamento, le crea.
    - Se l'utente non ha toccato le scadenze in questo form
      (``scadenze_formset.has_changed() == False``) le rigenera solo se
      forma di pagamento, data o totale sono cambiati rispetto a ``prima``
      (``base_scadenze`` letto prima del salvataggio): scadenze ritoccate a
      mano in un salvataggio precedente restano intatte.
    - Se invece l'utente ha modificato a mano le scadenze nel form,
      vengono lasciate intatte.
    - Mai su fatture pagate o già emesse verso il SDI.
    """
    user_touched = bool(scadenze_formset and scadenze_formset.has_changed())
    if user_touched:
        return
    sincronizza_scadenze(fattura, prima=prima)


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
            _maybe_genera_scadenze_auto(fattura, scadenze_formset=scad_fs)
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
        # letto prima della validazione: is_valid() scrive già sull'istanza
        prima = base_scadenze(fattura)
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
            _maybe_genera_scadenze_auto(
                fattura, scadenze_formset=formsets['scadenze_formset'], prima=prima,
            )
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

    def form_valid(self, form):
        # Una fattura trasmessa al SDI ha un numero ormai noto al SDI: se la
        # si cancella il progressivo verrebbe riassegnato a un'altra.
        if self.object.trasmessa_sdi:
            messages.error(
                self.request,
                f'La fattura {self.object.numero}/{self.object.anno} è già stata '
                f'trasmessa al SDI ({self.object.get_sdi_stato_display()}): non si '
                f'può eliminare. Per stornarla emetti una nota di credito.',
            )
            return redirect('documenti:fattura_detail', pk=self.object.pk)
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Toggle "pagata" rapido (HTMX) + endpoint per riga vuota inline
# ---------------------------------------------------------------------------


def fattura_toggle_pagata(request, pk):
    """Segna pagata (con la data, oggi se manca) o non pagata una fattura.

    ``azione``: ``pagata`` / ``non_pagata``; senza, inverte lo stato. Da HTMX
    (lista fatture) risponde con la cella dello stato, altrimenti torna al
    dettaglio."""
    if request.method != 'POST':
        return HttpResponseRedirect(reverse('documenti:fattura_detail', args=[pk]))
    from accounts.permissions import has_perm
    if not has_perm(request.user, 'documenti.modifica'):
        from django.http import HttpResponse
        return HttpResponse(status=403)
    from .servizi import DatiNonValidi, segna_pagata
    fattura = get_object_or_404(TestataFattura, pk=pk)
    azione = request.POST.get('azione') or ('non_pagata' if fattura.pagata else 'pagata')
    errore = ''
    if azione == 'pagata':
        try:
            segna_pagata(fattura, request.POST.get('data_pagamento'))
        except DatiNonValidi as e:
            errore = ' '.join(e.errori.values())
    elif fattura.pagata:
        fattura.pagata = False
        fattura.data_pagamento = None
        fattura.save(update_fields=['pagata', 'data_pagamento'])
    if getattr(request, 'htmx', False):
        return render(request, 'documenti/_fattura_pagamento.html',
                      {'f': fattura, 'errore': errore})
    if errore:
        messages.error(request, errore)
    else:
        messages.success(request, 'Fattura segnata come pagata.' if fattura.pagata
                         else 'Pagamento annullato.')
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
    return crea_pdf('documenti/pdf/documento.html', context, filename, request=request)


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

    residuo = scadenza.importo_residuo
    raw_importo = request.POST.get('importo', '').replace(',', '.').strip()
    try:
        importo = Decimal(raw_importo) if raw_importo else residuo
    except InvalidOperation:
        importo = None
    # NaN / Infinity passano il parsing ma non sono importi
    if importo is None or not importo.is_finite():
        messages.error(request, 'Importo non valido.')
        return redirect('documenti:fattura_acquisto_detail', pk=scadenza.fattura_id)
    importo = importo.quantize(Decimal('0.01'))
    if importo <= 0:
        messages.error(request, "L'importo del pagamento deve essere maggiore di zero.")
        return redirect('documenti:fattura_acquisto_detail', pk=scadenza.fattura_id)
    if importo > residuo:
        messages.error(
            request,
            f"L'importo supera il residuo da pagare della scadenza ({residuo} €).",
        )
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
    """UI + handler per importare fatture elettroniche di acquisto: uno o più
    file XML / .xml.p7m o zip (es. lo scaricamento multiplo di Aruba)."""
    from accounts.permissions import has_perm
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not has_perm(request.user, 'documenti.modifica'):
        messages.error(request, 'Non hai i permessi per importare fatture.')
        return redirect('documenti:fattura_list')

    ctx = {'active_nav': 'fatture_acquisto'}

    if request.method == 'POST':
        from .xml_import import importa_file

        uploads = request.FILES.getlist('xml_file')
        if not uploads:
            messages.error(request, "Seleziona almeno un file XML, .p7m o .zip.")
            return render(request, 'documenti/fattura_acquisto_import.html', ctx)
        if sum(u.size for u in uploads) > 50 * 1024 * 1024:
            messages.error(request, 'Caricamento troppo grande (max 50 MB in totale).')
            return render(request, 'documenti/fattura_acquisto_import.html', ctx)

        esiti = []
        for upload in uploads:
            esiti.extend(importa_file(upload.name, upload.read()))
        ordine = {'errore': 0, 'importata': 1, 'gia_presente': 2, 'ignorato': 3}
        esiti.sort(key=lambda e: ordine[e['esito']])
        conteggi = {k: sum(e['esito'] == k for e in esiti) for k in ordine}
        ctx.update({'esiti': esiti, 'conteggi': conteggi})
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
    from anagrafiche.models import AnagraficaAzienda

    azienda = AnagraficaAzienda.objects.first()
    totals = calcola_valori(fattura)
    pdf_ctx = {
        'fattura': fattura, 'azienda': azienda,
        'righe': fattura.righe.select_related('articolo', 'iva').all(),
        'scadenze': fattura.scadenze.all(),
        'totals': totals,
        'conto_corrente': resolve_conto_corrente(fattura, azienda),
    }
    pdf_bytes = render_pdf_bytes('documenti/pdf/fattura.html', pdf_ctx, request=request)

    # XML opzionale: lo allego solo se il cliente ha SDI/PEC configurati
    xml_bytes = None
    if fattura.cliente and fattura.cliente.fatturazione_elettronica_id:
        from .fatt_el import build_fattura_xml
        try:
            xml_bytes = build_fattura_xml(fattura)
        except ValueError as exc:  # anche FatturaElettronicaError
            messages.error(request, f'FatturaPA non generabile, email non inviata: {exc}')
            return redirect('documenti:fattura_detail', pk=pk)

    # Componi messaggio
    soggetto = (
        request.POST.get('subject')
        or f'Fattura {fattura.numero}/{fattura.anno}'
        + (f' — {azienda.ragione_sociale}' if azienda and azienda.ragione_sociale else '')
    )
    body = (
        request.POST.get('body')
        or f'In allegato la fattura {fattura.numero}/{fattura.anno}'
        + (f' del {fattura.data_documento:%d/%m/%Y}' if fattura.data_documento else '')
        + '.\n\n'
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
        from .fatt_el import nome_file_sdi
        msg.attach(
            nome_file_sdi(fattura, azienda.partita_iva if azienda else ''),
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

    from .fatt_el import build_fattura_xml, nome_file_sdi
    from .sdi import get_backend
    from anagrafiche.models import AnagraficaAzienda
    from .models import MessaggioSdi

    try:
        backend = get_backend()
    except ValueError as exc:  # configurazione PEC incompleta
        messages.error(request, str(exc))
        return redirect('documenti:fattura_detail', pk=pk)
    if backend is None:
        messages.error(
            request,
            "Backend SDI non configurato sull'anagrafica azienda. "
            "Imposta sdi_provider e (se PEC) le credenziali SMTP.",
        )
        return redirect('documenti:fattura_detail', pk=pk)

    # Reinvio dopo scarto/rifiuto: il SDI non accetta due volte lo stesso nome
    # file, quindi il tentativo (che entra nel progressivo) avanza. Si salva
    # solo se l'invio riesce.
    if fattura.sdi_stato in TestataFattura.SDI_STATI_REINVIO:
        if fattura.tentativo_sdi >= 35:
            messages.error(request, 'Raggiunto il numero massimo di reinvii al SDI per questa fattura.')
            return redirect('documenti:fattura_detail', pk=pk)
        fattura.tentativo_sdi += 1

    try:
        xml_bytes = build_fattura_xml(fattura)
        az = AnagraficaAzienda.objects.first()
        filename = nome_file_sdi(fattura, az.partita_iva if az else '')
        result = backend.send_xml(fattura, xml_bytes, filename)
    except ValueError as exc:  # anche FatturaElettronicaError
        messages.error(request, f'Impossibile generare la FatturaPA: {exc}')
        return redirect('documenti:fattura_detail', pk=pk)
    from django.utils.timezone import now as _now
    if result.success:
        TestataFattura.objects.filter(pk=fattura.pk).update(
            sdi_stato=TestataFattura.SdiStato.INVIATA,
            sdi_data_invio=_now(),
            sdi_id_trasmissione=result.id_trasmissione or '',
            sdi_ultimo_messaggio=result.descrizione,
            tentativo_sdi=fattura.tentativo_sdi,
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
    from .fatt_el import build_fattura_xml, nome_file_sdi
    fattura = get_object_or_404(TestataFattura, pk=pk)
    try:
        xml_bytes = build_fattura_xml(fattura)
    except ValueError as exc:  # anche FatturaElettronicaError
        messages.error(request, f'FatturaPA non generabile: {exc}')
        return redirect('documenti:fattura_detail', pk=pk)
    # Nome file conforme SDI: IT<PIVA>_<progressivo>.xml
    from anagrafiche.models import AnagraficaAzienda
    az = AnagraficaAzienda.objects.first()
    filename = nome_file_sdi(fattura, az.partita_iva if az else '')
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
    from anagrafiche.models import AnagraficaAzienda

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
                'conto_corrente': resolve_conto_corrente(f, azienda),
            }
            pdf_bytes = render_pdf_bytes(
                'documenti/pdf/fattura.html', ctx, request=request,
            )
            zf.writestr(f'fattura_{f.numero}_{f.anno}.pdf', pdf_bytes)

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
        'conto_corrente': resolve_conto_corrente(fattura, azienda),
    }
    # Render PDF in memoria (riuso lo stesso template usato dalla view non firmata).
    pdf_bytes = render_pdf_bytes(
        'documenti/pdf/fattura.html', context, request=request,
    )

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
        'conto_corrente': resolve_conto_corrente(fattura, azienda),
    }
    filename = f'fattura_{fattura.numero}_{fattura.anno}.pdf'
    return crea_pdf('documenti/pdf/fattura.html', context, filename, request=request)


def fattura_riga_form_empty(request):
    """Restituisce un form-row vuoto per il formset righe (HTMX append)."""
    from accounts.permissions import has_perm
    from django.http import HttpResponse as _Hr, HttpResponseBadRequest
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not has_perm(request.user, 'documenti.vedi'):
        return _Hr(status=403)
    raw_index = (request.GET.get('index') or '0').strip()
    if not raw_index.isdigit() or len(raw_index) > 4:
        return HttpResponseBadRequest('Indice non valido.')
    index = int(raw_index)
    formset = RigaFatturaFormSet(prefix='righe')
    # Forziamo total_forms al valore richiesto per evitare collisioni di name=
    formset.management_form.initial['TOTAL_FORMS'] = index + 1
    empty_form = formset.empty_form
    return render(
        request,
        'documenti/_riga_row.html',
        {'form': empty_form, 'index': index},
    )
