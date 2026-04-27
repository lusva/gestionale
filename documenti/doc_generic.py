"""
Pattern generico per il CRUD dei documenti che condividono lo schema
testata + righe (Offerta, Ordine, DDT, NotaCredito, DdtFornitore).

Le fatture cliente hanno un flusso a sé (scadenze + FatturaPA + PDF) e
restano definite in ``views.py``.

Uso:

    make_doc_views(
        model=TestataOfferta,
        form_class=TestataOffertaForm,
        riga_formset_class=RigaOffertaFormSet,
        label='Offerta',
        label_plural='Offerte',
        active_nav='offerte',
        url_prefix='offerta',
        counterparty='cliente',          # 'cliente' | 'fornitore'
        has_stato=True,                  # mostra il filtro stato
    )
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Q
from django.forms import inlineformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import DeleteView, DetailView, ListView, TemplateView

from accounts.permissions import PermRequiredMixin

from .models import RigaDocumento


def make_riga_formset(parent_model, fk_name, form_class):
    return inlineformset_factory(
        parent_model=parent_model,
        model=RigaDocumento,
        form=form_class,
        fk_name=fk_name,
        extra=1,
        can_delete=True,
    )


def make_doc_views(*, model, form_class, riga_formset_class, label, label_plural,
                    active_nav, url_prefix, counterparty='cliente', has_stato=False):
    """Crea 5 classi CBV (List/Detail/Create/Update/Delete) per un modello testata."""

    # Alias locali: i nomi `model`/`form_class` verrebbero ombrati nella
    # class body delle CBV se usassi gli stessi identificatori.
    _model = model
    _form_class = form_class

    urls = {
        'list':   f'documenti:{url_prefix}_list',
        'create': f'documenti:{url_prefix}_create',
        'detail': f'documenti:{url_prefix}_detail',
        'update': f'documenti:{url_prefix}_update',
        'delete': f'documenti:{url_prefix}_delete',
    }

    shared_ctx = {
        'doc_label': label,
        'doc_label_plural': label_plural,
        'active_nav': active_nav,
        'counterparty': counterparty,
        'has_stato': has_stato,
        'urls': urls,
    }

    class _BaseList(LoginRequiredMixin, PermRequiredMixin, ListView):
        required_perm = 'documenti.vedi'
        template_name = 'documenti/doc_list.html'
        context_object_name = 'documenti'
        paginate_by = 20

        def get_queryset(self):
            qs = _model.objects.select_related(counterparty).order_by('-anno', '-numero')
            q = (self.request.GET.get('q') or '').strip()
            if q:
                if counterparty == 'cliente':
                    qs = qs.filter(
                        Q(numero__icontains=q)
                        | Q(cliente__ragione_sociale__icontains=q)
                        | Q(cliente__partita_iva__icontains=q)
                        | Q(note__icontains=q),
                    )
                else:
                    qs = qs.filter(
                        Q(numero__icontains=q)
                        | Q(fornitore__ragione_sociale__icontains=q)
                        | Q(fornitore__partita_iva__icontains=q)
                        | Q(note__icontains=q),
                    )
            anno = (self.request.GET.get('anno') or '').strip()
            if anno.isdigit():
                qs = qs.filter(anno=int(anno))
            if has_stato:
                stato = self.request.GET.get('stato')
                if stato:
                    qs = qs.filter(stato=stato)
            return qs

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.update(shared_ctx)
            ctx['q'] = self.request.GET.get('q', '')
            ctx['anno'] = self.request.GET.get('anno', '')
            ctx['anni_choices'] = list(
                _model.objects.values_list('anno', flat=True).distinct().order_by('-anno'),
            )
            if has_stato:
                ctx['stato'] = self.request.GET.get('stato', '')
                ctx['stato_choices'] = _model._meta.get_field('stato').choices
            return ctx

    class _BaseDetail(LoginRequiredMixin, PermRequiredMixin, DetailView):
        required_perm = 'documenti.vedi'
        model = _model
        template_name = 'documenti/doc_detail.html'
        context_object_name = 'documento'

        def get_queryset(self):
            return _model.objects.select_related(
                counterparty, 'forme_pagamento', 'conto_corrente',
            )

        def get_context_data(self, **kwargs):
            from decimal import Decimal
            from .models import (
                TestataDdt, TestataFattura, TestataNotaCredito,
                TestataOfferta, TestataOrdine,
            )
            ctx = super().get_context_data(**kwargs)
            ctx.update(shared_ctx)
            doc = self.object
            righe = list(doc.righe.select_related('articolo', 'iva').all())
            imp = sum((r.imponibile for r in righe), Decimal('0'))
            imposta = sum((r.imposta for r in righe), Decimal('0'))
            ctx['righe'] = righe
            ctx['imponibile_calc'] = imp
            ctx['imposta_calc'] = imposta
            ctx['totale_calc'] = imp + imposta

            # Catena documenti collegati: navigo le M2M correlate al modello
            # corrente per costruire una lista omogenea ``[{tipo, doc,
            # url_name, verso}]`` consumabile dal template.
            chain = []
            if isinstance(doc, TestataOfferta):
                for o in doc.ordine_collegato.all():
                    chain.append({'tipo': 'Ordine', 'doc': o,
                                  'url_name': 'documenti:ordine_detail',
                                  'verso': 'avanti'})
            elif isinstance(doc, TestataOrdine):
                for o in doc.offerte_origine.all():
                    chain.append({'tipo': 'Offerta', 'doc': o,
                                  'url_name': 'documenti:offerta_detail',
                                  'verso': 'indietro'})
                for d in doc.ddt_collegato.all():
                    chain.append({'tipo': 'DDT', 'doc': d,
                                  'url_name': 'documenti:ddt_detail',
                                  'verso': 'avanti'})
                for f in doc.fattura_collegata.all():
                    chain.append({'tipo': 'Fattura', 'doc': f,
                                  'url_name': 'documenti:fattura_detail',
                                  'verso': 'avanti'})
            elif isinstance(doc, TestataDdt):
                for o in doc.ordini_origine.all():
                    chain.append({'tipo': 'Ordine', 'doc': o,
                                  'url_name': 'documenti:ordine_detail',
                                  'verso': 'indietro'})
            elif isinstance(doc, TestataNotaCredito):
                for f in doc.fatture_origine.all():
                    chain.append({'tipo': 'Fattura', 'doc': f,
                                  'url_name': 'documenti:fattura_detail',
                                  'verso': 'indietro'})
            ctx['chain'] = chain
            return ctx

    class _SaveMixin:
        template_name = 'documenti/doc_form.html'

        def _formsets(self, instance, post_data=None):
            return {
                'righe_formset': riga_formset_class(
                    post_data, instance=instance, prefix='righe',
                ),
            }

        def _render(self, request, form, formsets, *, instance=None, is_create=True):
            ctx = dict(shared_ctx)
            ctx.update({
                'form': form,
                'object': instance,
                'documento': instance,
                'is_create': is_create,
                **formsets,
            })
            return render(request, self.template_name, ctx)

    class _Create(LoginRequiredMixin, PermRequiredMixin, _SaveMixin, TemplateView):
        required_perm = 'documenti.modifica'

        def get(self, request, *args, **kwargs):
            return self._render(
                request, _form_class(), self._formsets(_model()), is_create=True,
            )

        def post(self, request, *args, **kwargs):
            form = _form_class(request.POST)
            if not form.is_valid():
                return self._render(
                    request, form, self._formsets(_model(), request.POST),
                    is_create=True,
                )
            with transaction.atomic():
                doc = form.save()
                fs = self._formsets(doc, request.POST)
                if not fs['righe_formset'].is_valid():
                    doc.delete()
                    return self._render(request, form, fs, is_create=True)
                fs['righe_formset'].save()
            messages.success(
                request,
                f'{label} {doc.numero}/{doc.anno} creata con successo.',
            )
            return redirect(urls['detail'], pk=doc.pk)

    class _Update(LoginRequiredMixin, PermRequiredMixin, _SaveMixin, TemplateView):
        required_perm = 'documenti.modifica'

        def get(self, request, pk, *args, **kwargs):
            doc = get_object_or_404(_model, pk=pk)
            return self._render(
                request, _form_class(instance=doc), self._formsets(doc),
                instance=doc, is_create=False,
            )

        def post(self, request, pk, *args, **kwargs):
            doc = get_object_or_404(_model, pk=pk)
            form = _form_class(request.POST, instance=doc)
            fs = self._formsets(doc, request.POST)
            if not (form.is_valid() and fs['righe_formset'].is_valid()):
                return self._render(request, form, fs, instance=doc, is_create=False)
            with transaction.atomic():
                form.save()
                fs['righe_formset'].save()
            messages.success(request, f'{label} aggiornata.')
            return redirect(urls['detail'], pk=doc.pk)

    class _Delete(LoginRequiredMixin, PermRequiredMixin, DeleteView):
        required_perm = 'documenti.elimina'
        model = _BaseDetail.model
        template_name = 'documenti/confirm_delete.html'
        success_url = reverse_lazy(urls['list'])

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.update(shared_ctx)
            ctx['object_label'] = f'{label} {self.object.numero}/{self.object.anno}'
            ctx['back_url'] = reverse_lazy(urls['detail'], args=[self.object.pk])
            return ctx

    return _BaseList, _BaseDetail, _Create, _Update, _Delete
