"""Viste per il modulo Cashflow."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import (
    CreateView, DeleteView, ListView, TemplateView, UpdateView,
)

from accounts.permissions import PermRequiredMixin

from .forms import ScadenzaFiscaleForm, SpesaRicorrenteForm
from .models import (
    ScadenzaFiscale, ScadenzaSpesa, SpesaRicorrente, TipoScadenzaFiscale,
)


# ---------------------------------------------------------------------------
# Scadenze fiscali
# ---------------------------------------------------------------------------


class ScadenzaFiscaleListView(LoginRequiredMixin, PermRequiredMixin, ListView):
    required_perm = 'cashflow.vedi'
    model = ScadenzaFiscale
    template_name = 'cashflow/scadenza_fiscale_list.html'
    context_object_name = 'scadenze'
    paginate_by = 30

    def get_queryset(self):
        qs = ScadenzaFiscale.objects.all()
        stato = (self.request.GET.get('stato') or '').strip()
        if stato == 'aperte':
            qs = qs.filter(pagata=False)
        elif stato == 'pagate':
            qs = qs.filter(pagata=True)
        elif stato == 'scadute':
            qs = qs.filter(pagata=False, data_scadenza__lt=timezone.localdate())
        tipo = (self.request.GET.get('tipo') or '').strip()
        if tipo:
            qs = qs.filter(tipo=tipo)
        q = (self.request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(descrizione__icontains=q)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'scadenze_fiscali'
        ctx['stato'] = self.request.GET.get('stato', '')
        ctx['tipo'] = self.request.GET.get('tipo', '')
        ctx['q'] = self.request.GET.get('q', '')
        ctx['tipi'] = TipoScadenzaFiscale.choices
        oggi = timezone.localdate()
        ctx['today'] = oggi
        aperte = ScadenzaFiscale.objects.filter(pagata=False)
        ctx['tot_aperte'] = sum((s.importo for s in aperte), Decimal('0'))
        ctx['tot_scadute'] = sum(
            (s.importo for s in aperte if s.data_scadenza < oggi), Decimal('0'),
        )
        return ctx


class ScadenzaFiscaleCreateView(LoginRequiredMixin, PermRequiredMixin, CreateView):
    required_perm = 'cashflow.modifica'
    model = ScadenzaFiscale
    form_class = ScadenzaFiscaleForm
    template_name = 'cashflow/scadenza_fiscale_form.html'

    def get_success_url(self):
        return reverse('cashflow:scadenza_fiscale_list')

    def form_valid(self, form):
        messages.success(self.request, 'Scadenza fiscale creata.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'scadenze_fiscali'
        ctx['is_create'] = True
        return ctx


class ScadenzaFiscaleUpdateView(LoginRequiredMixin, PermRequiredMixin, UpdateView):
    required_perm = 'cashflow.modifica'
    model = ScadenzaFiscale
    form_class = ScadenzaFiscaleForm
    template_name = 'cashflow/scadenza_fiscale_form.html'

    def get_success_url(self):
        return reverse('cashflow:scadenza_fiscale_list')

    def form_valid(self, form):
        messages.success(self.request, 'Scadenza fiscale aggiornata.')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'scadenze_fiscali'
        ctx['is_create'] = False
        return ctx


class ScadenzaFiscaleDeleteView(LoginRequiredMixin, PermRequiredMixin, DeleteView):
    required_perm = 'cashflow.elimina'
    model = ScadenzaFiscale
    template_name = 'cashflow/confirm_delete.html'
    success_url = reverse_lazy('cashflow:scadenza_fiscale_list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'scadenze_fiscali'
        ctx['object_label'] = str(self.object)
        ctx['back_url'] = reverse('cashflow:scadenza_fiscale_list')
        return ctx

    def form_valid(self, form):
        messages.success(self.request, 'Scadenza fiscale eliminata.')
        return super().form_valid(form)


def scadenza_fiscale_marca_pagata(request, pk):
    if not request.user.is_authenticated:
        return redirect('accounts:login')
    s = get_object_or_404(ScadenzaFiscale, pk=pk)
    s.pagata = not s.pagata
    s.data_pagamento = timezone.localdate() if s.pagata else None
    s.save(update_fields=['pagata', 'data_pagamento', 'updated_at'])
    messages.success(
        request,
        'Marcata come pagata.' if s.pagata else 'Pagamento annullato.',
    )
    next_url = request.GET.get('next') or request.META.get('HTTP_REFERER')
    return HttpResponseRedirect(next_url or reverse('cashflow:scadenza_fiscale_list'))


# ---------------------------------------------------------------------------
# Spese ricorrenti
# ---------------------------------------------------------------------------


class SpesaRicorrenteListView(LoginRequiredMixin, PermRequiredMixin, ListView):
    required_perm = 'cashflow.vedi'
    model = SpesaRicorrente
    template_name = 'cashflow/spesa_ricorrente_list.html'
    context_object_name = 'spese'
    paginate_by = 30

    def get_queryset(self):
        qs = SpesaRicorrente.objects.select_related('categoria_costo', 'fornitore')
        stato = (self.request.GET.get('stato') or '').strip()
        if stato == 'attive':
            qs = qs.filter(attiva=True)
        elif stato == 'inattive':
            qs = qs.filter(attiva=False)
        q = (self.request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(descrizione__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'spese_ricorrenti'
        ctx['stato'] = self.request.GET.get('stato', '')
        ctx['q'] = self.request.GET.get('q', '')
        return ctx


class SpesaRicorrenteCreateView(LoginRequiredMixin, PermRequiredMixin, CreateView):
    required_perm = 'cashflow.modifica'
    model = SpesaRicorrente
    form_class = SpesaRicorrenteForm
    template_name = 'cashflow/spesa_ricorrente_form.html'

    def get_success_url(self):
        return reverse('cashflow:spesa_ricorrente_detail', args=[self.object.pk])

    def form_valid(self, form):
        response = super().form_valid(form)
        fino_a = timezone.localdate() + timedelta(days=180)
        nuove = self.object.genera_scadenze(fino_a)
        messages.success(
            self.request,
            f'Spesa ricorrente creata. Generate {nuove} scadenze fino al {fino_a:%d/%m/%Y}.',
        )
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'spese_ricorrenti'
        ctx['is_create'] = True
        return ctx


class SpesaRicorrenteUpdateView(LoginRequiredMixin, PermRequiredMixin, UpdateView):
    required_perm = 'cashflow.modifica'
    model = SpesaRicorrente
    form_class = SpesaRicorrenteForm
    template_name = 'cashflow/spesa_ricorrente_form.html'

    def get_success_url(self):
        return reverse('cashflow:spesa_ricorrente_detail', args=[self.object.pk])

    def form_valid(self, form):
        response = super().form_valid(form)
        fino_a = timezone.localdate() + timedelta(days=180)
        nuove = self.object.genera_scadenze(fino_a)
        if nuove:
            messages.success(
                self.request,
                f'Spesa aggiornata. {nuove} nuove scadenze generate.',
            )
        else:
            messages.success(self.request, 'Spesa aggiornata.')
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'spese_ricorrenti'
        ctx['is_create'] = False
        return ctx


class SpesaRicorrenteDetailView(LoginRequiredMixin, PermRequiredMixin, TemplateView):
    required_perm = 'cashflow.vedi'
    template_name = 'cashflow/spesa_ricorrente_detail.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'spese_ricorrenti'
        spesa = get_object_or_404(SpesaRicorrente, pk=kwargs['pk'])
        ctx['spesa'] = spesa
        ctx['scadenze'] = spesa.scadenze.order_by('data_scadenza')
        ctx['today'] = timezone.localdate()
        return ctx


class SpesaRicorrenteDeleteView(LoginRequiredMixin, PermRequiredMixin, DeleteView):
    required_perm = 'cashflow.elimina'
    model = SpesaRicorrente
    template_name = 'cashflow/confirm_delete.html'
    success_url = reverse_lazy('cashflow:spesa_ricorrente_list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'spese_ricorrenti'
        ctx['object_label'] = str(self.object)
        ctx['back_url'] = reverse('cashflow:spesa_ricorrente_list')
        return ctx

    def form_valid(self, form):
        messages.success(self.request, 'Spesa ricorrente eliminata.')
        return super().form_valid(form)


def scadenza_spesa_marca_pagata(request, pk):
    if not request.user.is_authenticated:
        return redirect('accounts:login')
    s = get_object_or_404(ScadenzaSpesa, pk=pk)
    s.pagata = not s.pagata
    s.data_pagamento = timezone.localdate() if s.pagata else None
    s.save(update_fields=['pagata', 'data_pagamento', 'updated_at'])
    messages.success(
        request,
        'Marcata come pagata.' if s.pagata else 'Pagamento annullato.',
    )
    next_url = request.GET.get('next') or request.META.get('HTTP_REFERER')
    return HttpResponseRedirect(next_url or reverse('cashflow:spesa_ricorrente_list'))


# ---------------------------------------------------------------------------
# Cashflow timeline
# ---------------------------------------------------------------------------


class CashflowView(LoginRequiredMixin, PermRequiredMixin, TemplateView):
    required_perm = 'cashflow.vedi'
    template_name = 'cashflow/cashflow.html'

    def get_context_data(self, **kwargs):
        from documenti.models import (
            ScadenzaFattura, ScadenzaFatturaAcquisto,
        )
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'cashflow'

        oggi = timezone.localdate()
        try:
            giorni = int(self.request.GET.get('giorni', '90'))
        except (TypeError, ValueError):
            giorni = 90
        giorni = max(7, min(giorni, 365))
        fino_a = oggi + timedelta(days=giorni)

        eventi: list[dict] = []

        # Incassi: scadenze fatture cliente non pagate.
        for s in ScadenzaFattura.objects.select_related(
            'fattura', 'fattura__cliente',
        ).filter(fattura__pagata=False, data__lte=fino_a):
            cli = s.fattura.cliente.ragione_sociale if s.fattura.cliente else ''
            eventi.append({
                'data': s.data,
                'tipo': 'incasso',
                'origine': 'Fattura cliente',
                'descrizione': f'Fatt. {s.fattura.numero}/{s.fattura.anno} — {cli}',
                'importo': s.importo,
                'scaduta': s.data < oggi,
                'url': None,
            })

        # Uscite: scadenze fatture acquisto con residuo > 0.
        for s in ScadenzaFatturaAcquisto.objects.select_related(
            'fattura', 'fattura__fornitore',
        ).filter(data_scadenza__lte=fino_a):
            residuo = s.importo_residuo
            if residuo <= 0:
                continue
            forn = s.fattura.fornitore.ragione_sociale if s.fattura.fornitore else ''
            eventi.append({
                'data': s.data_scadenza,
                'tipo': 'uscita',
                'origine': 'Fattura fornitore',
                'descrizione': f'Fatt. acq. — {forn}',
                'importo': residuo,
                'scaduta': s.data_scadenza < oggi,
                'url': None,
            })

        # Uscite: scadenze fiscali aperte.
        for s in ScadenzaFiscale.objects.filter(pagata=False, data_scadenza__lte=fino_a):
            eventi.append({
                'data': s.data_scadenza,
                'tipo': 'uscita',
                'origine': f'Fiscale ({s.get_tipo_display()})',
                'descrizione': s.descrizione,
                'importo': s.importo,
                'scaduta': s.data_scadenza < oggi,
                'url': reverse('cashflow:scadenza_fiscale_update', args=[s.pk]),
            })

        # Uscite: scadenze spese ricorrenti aperte.
        for s in ScadenzaSpesa.objects.select_related('spesa').filter(
            pagata=False, data_scadenza__lte=fino_a,
        ):
            eventi.append({
                'data': s.data_scadenza,
                'tipo': 'uscita',
                'origine': 'Spesa ricorrente',
                'descrizione': s.spesa.descrizione,
                'importo': s.importo,
                'scaduta': s.data_scadenza < oggi,
                'url': reverse('cashflow:spesa_ricorrente_detail', args=[s.spesa_id]),
            })

        eventi.sort(key=lambda e: (e['data'], e['tipo']))

        saldo = Decimal('0')
        for e in eventi:
            if e['tipo'] == 'incasso':
                saldo += e['importo']
            else:
                saldo -= e['importo']
            e['saldo'] = saldo

        tot_incassi = sum((e['importo'] for e in eventi if e['tipo'] == 'incasso'), Decimal('0'))
        tot_uscite = sum((e['importo'] for e in eventi if e['tipo'] == 'uscita'), Decimal('0'))
        tot_scadute_in = sum(
            (e['importo'] for e in eventi if e['tipo'] == 'incasso' and e['scaduta']),
            Decimal('0'),
        )
        tot_scadute_out = sum(
            (e['importo'] for e in eventi if e['tipo'] == 'uscita' and e['scaduta']),
            Decimal('0'),
        )

        ctx.update({
            'eventi': eventi,
            'today': oggi,
            'fino_a': fino_a,
            'giorni': giorni,
            'finestre': [30, 60, 90, 180, 365],
            'tot_incassi': tot_incassi,
            'tot_uscite': tot_uscite,
            'saldo_finale': tot_incassi - tot_uscite,
            'tot_scadute_in': tot_scadute_in,
            'tot_scadute_out': tot_scadute_out,
        })
        return ctx
