"""
Factory CRUD generico per le anagrafiche semplici (senza inline).

Copre Fornitore, Articolo, PosizioneIva, CategoriaCosto. La FormePagamento
ha le scadenze inline e ha il suo modulo dedicato in ``views_form_pag.py``.
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from accounts.permissions import PermRequiredMixin


def make_anag_views(*, model, form_class, label, label_plural,
                     active_nav, url_prefix, search_fields,
                     list_display, perm_view='clienti.vedi',
                     perm_modifica='clienti.modifica',
                     perm_elimina='clienti.elimina',
                     filter_field=None, filter_choices=None):
    """Crea 5 CBV (List/Detail/Create/Update/Delete) per un modello anagrafico semplice.

    ``search_fields``: tuple di campi su cui fare icontains via parametro ``q``
    ``list_display``: lista di tuple ``(field_name, label, css_class)`` per la tabella
    ``filter_field``/``filter_choices``: opzionalmente espone un selettore filtro
    """

    _model = model
    _form_class = form_class

    urls = {
        'list':   f'anagrafiche:{url_prefix}_list',
        'create': f'anagrafiche:{url_prefix}_create',
        'detail': f'anagrafiche:{url_prefix}_detail',
        'update': f'anagrafiche:{url_prefix}_update',
        'delete': f'anagrafiche:{url_prefix}_delete',
    }

    shared_ctx = {
        'anag_label': label,
        'anag_label_plural': label_plural,
        'active_nav': active_nav,
        'urls': urls,
        'list_display': list_display,
        'filter_field': filter_field,
        'filter_choices': filter_choices,
    }

    class _List(LoginRequiredMixin, PermRequiredMixin, ListView):
        required_perm = perm_view
        template_name = 'anagrafiche/anag_list.html'
        context_object_name = 'oggetti'
        paginate_by = 25

        def get_queryset(self):
            qs = _model.objects.all()
            # Soft-delete su modelli che lo supportano (Fornitore)
            if hasattr(_model, 'soft_delete'):
                qs = qs.filter(soft_delete=False)
            q = (self.request.GET.get('q') or '').strip()
            if q and search_fields:
                cond = Q()
                for f in search_fields:
                    cond |= Q(**{f'{f}__icontains': q})
                qs = qs.filter(cond)
            if filter_field:
                fv = (self.request.GET.get(filter_field) or '').strip()
                if fv:
                    qs = qs.filter(**{filter_field: fv})
            return qs

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.update(shared_ctx)
            ctx['q'] = self.request.GET.get('q', '')
            if filter_field:
                ctx['filter_value'] = self.request.GET.get(filter_field, '')
            return ctx

        def get_template_names(self):
            if self.request.htmx and not self.request.htmx.boosted:
                return ['anagrafiche/_anag_table.html']
            return [self.template_name]

    class _Detail(LoginRequiredMixin, PermRequiredMixin, DetailView):
        required_perm = perm_view
        model = _model
        template_name = 'anagrafiche/anag_detail.html'
        context_object_name = 'oggetto'

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.update(shared_ctx)
            # Pre-renderizzo coppie (label, value) — il template non può
            # accedere a ``_meta.fields`` direttamente (Django blocca i
            # nomi che iniziano con underscore).
            obj = self.object
            details = []
            for f in obj._meta.fields:
                if f.name in ('id', 'soft_delete'):
                    continue
                value = getattr(obj, f.name, None)
                # Per FK uso lo str dell'oggetto collegato
                if hasattr(f, 'related_model') and f.related_model is not None:
                    value = getattr(obj, f.name, None)
                details.append({
                    'label': f.verbose_name or f.name.replace('_', ' '),
                    'value': value,
                })
            ctx['details'] = details
            return ctx

    class _Create(LoginRequiredMixin, PermRequiredMixin, CreateView):
        required_perm = perm_modifica
        model = _model
        form_class = _form_class
        template_name = 'anagrafiche/anag_form.html'

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.update(shared_ctx)
            ctx['is_create'] = True
            return ctx

        def form_valid(self, form):
            messages.success(self.request, f'{label} creato/a.')
            return super().form_valid(form)

        def get_success_url(self):
            return reverse_lazy(urls['detail'], args=[self.object.pk])

    class _Update(LoginRequiredMixin, PermRequiredMixin, UpdateView):
        required_perm = perm_modifica
        model = _model
        form_class = _form_class
        template_name = 'anagrafiche/anag_form.html'

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.update(shared_ctx)
            ctx['is_create'] = False
            return ctx

        def form_valid(self, form):
            messages.success(self.request, f'{label} aggiornato/a.')
            return super().form_valid(form)

        def get_success_url(self):
            return reverse_lazy(urls['detail'], args=[self.object.pk])

    class _Delete(LoginRequiredMixin, PermRequiredMixin, DeleteView):
        required_perm = perm_elimina
        model = _model
        template_name = 'anagrafiche/confirm_delete.html'
        success_url = reverse_lazy(urls['list'])

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.update(shared_ctx)
            ctx['object_label'] = str(self.object)
            return ctx

        def form_valid(self, form):
            # Soft-delete se il modello lo supporta, altrimenti hard delete
            obj = self.get_object()
            if hasattr(obj, 'soft_delete'):
                obj.soft_delete = True
                obj.save(update_fields=['soft_delete'])
                messages.success(self.request, f'{label} eliminato/a (soft delete).')
                return redirect(self.success_url)
            messages.success(self.request, f'{label} eliminato/a.')
            return super().form_valid(form)

    return _List, _Detail, _Create, _Update, _Delete
