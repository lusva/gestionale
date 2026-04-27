"""Viste HTMX per le anagrafiche fiscali."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render

from accounts.permissions import require_perm

from .crud_generic import make_anag_views
from .forms import (
    AnagraficaAziendaForm,
    ArticoloForm,
    CategoriaCostoForm,
    FormePagamentoForm,
    FornitoreForm,
    PosizioneIvaForm,
    ScadenzaFormSet,
)
from .models import (
    AnagraficaAzienda,
    Articolo,
    CategoriaCosto,
    FormePagamento,
    Fornitore,
    PosizioneIva,
)


# ---------------------------------------------------------------------------
# CRUD generico per i modelli senza inline
# ---------------------------------------------------------------------------


(FornitoreListView, FornitoreDetailView, FornitoreCreateView,
 FornitoreUpdateView, FornitoreDeleteView) = make_anag_views(
    model=Fornitore,
    form_class=FornitoreForm,
    label='Fornitore',
    label_plural='Fornitori',
    active_nav='fornitori',
    url_prefix='fornitore',
    search_fields=('ragione_sociale', 'nome', 'cognome', 'partita_iva', 'codice_fiscale', 'email'),
    list_display=[
        ('ragione_sociale', 'Ragione sociale', ''),
        ('partita_iva', 'P.IVA', 'mono'),
        ('email', 'Email', ''),
        ('telefono', 'Telefono', ''),
    ],
)


(ArticoloListView, ArticoloDetailView, ArticoloCreateView,
 ArticoloUpdateView, ArticoloDeleteView) = make_anag_views(
    model=Articolo,
    form_class=ArticoloForm,
    label='Articolo',
    label_plural='Articoli',
    active_nav='articoli',
    url_prefix='articolo',
    search_fields=('codice', 'descrizione', 'scelta'),
    list_display=[
        ('codice', 'Codice', 'mono'),
        ('descrizione', 'Descrizione', ''),
        ('um', 'UM', ''),
        ('prezzo_listino', 'Prezzo', 'mono'),
        ('obsoleto', 'Obsoleto', ''),
    ],
)


(PosizioneIvaListView, PosizioneIvaDetailView, PosizioneIvaCreateView,
 PosizioneIvaUpdateView, PosizioneIvaDeleteView) = make_anag_views(
    model=PosizioneIva,
    form_class=PosizioneIvaForm,
    label='Posizione IVA',
    label_plural='Posizioni IVA',
    active_nav='posizioni_iva',
    url_prefix='posizione_iva',
    search_fields=('descrizione',),
    list_display=[
        ('descrizione', 'Descrizione', ''),
        ('aliquota', 'Aliquota %', 'mono'),
        ('natura', 'Natura', 'mono'),
        ('reverse_charge', 'Reverse', ''),
        ('esente', 'Esente', ''),
    ],
)


(CategoriaCostoListView, CategoriaCostoDetailView, CategoriaCostoCreateView,
 CategoriaCostoUpdateView, CategoriaCostoDeleteView) = make_anag_views(
    model=CategoriaCosto,
    form_class=CategoriaCostoForm,
    label='Categoria costo',
    label_plural='Categorie costo',
    active_nav='categorie_costo',
    url_prefix='categoria_costo',
    search_fields=('codice', 'descrizione'),
    list_display=[
        ('codice', 'Codice', 'mono'),
        ('descrizione', 'Descrizione', ''),
        ('ordinamento', 'Ordine', 'mono'),
        ('attiva', 'Attiva', ''),
    ],
)


# ---------------------------------------------------------------------------
# FormePagamento con scadenze inline
# ---------------------------------------------------------------------------


@login_required
@require_perm('clienti.vedi')
def forme_pagamento_list(request):
    qs = FormePagamento.objects.prefetch_related('scadenze').all()
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(tipo_pagamento__icontains=q)
    return render(request, 'anagrafiche/forme_pagamento_list.html', {
        'active_nav': 'forme_pagamento',
        'forme': qs,
        'q': q,
    })


def _forma_pagamento_form_view(request, pk=None):
    fp = FormePagamento.objects.get(pk=pk) if pk else None
    is_create = fp is None
    if request.method == 'POST':
        form = FormePagamentoForm(request.POST, instance=fp)
        if not form.is_valid():
            scad_fs = ScadenzaFormSet(request.POST, instance=fp or FormePagamento(), prefix='scad')
            return render(request, 'anagrafiche/forma_pagamento_form.html', {
                'active_nav': 'forme_pagamento',
                'form': form, 'scad_formset': scad_fs,
                'is_create': is_create, 'object': fp,
            })
        with transaction.atomic():
            obj = form.save()
            scad_fs = ScadenzaFormSet(request.POST, instance=obj, prefix='scad')
            if not scad_fs.is_valid():
                if is_create:
                    obj.delete()
                return render(request, 'anagrafiche/forma_pagamento_form.html', {
                    'active_nav': 'forme_pagamento',
                    'form': form, 'scad_formset': scad_fs,
                    'is_create': is_create, 'object': fp,
                })
            scad_fs.save()
        messages.success(request, 'Forma di pagamento salvata.')
        return redirect('anagrafiche:forme_pagamento_list')
    form = FormePagamentoForm(instance=fp)
    scad_fs = ScadenzaFormSet(instance=fp or FormePagamento(), prefix='scad')
    return render(request, 'anagrafiche/forma_pagamento_form.html', {
        'active_nav': 'forme_pagamento',
        'form': form, 'scad_formset': scad_fs,
        'is_create': is_create, 'object': fp,
    })


@login_required
@require_perm('clienti.modifica')
def forma_pagamento_create(request):
    return _forma_pagamento_form_view(request)


@login_required
@require_perm('clienti.modifica')
def forma_pagamento_update(request, pk):
    return _forma_pagamento_form_view(request, pk=pk)


@login_required
@require_perm('clienti.elimina')
def forma_pagamento_delete(request, pk):
    fp = FormePagamento.objects.get(pk=pk)
    if request.method == 'POST':
        fp.delete()
        messages.success(request, 'Forma di pagamento eliminata.')
        return redirect('anagrafiche:forme_pagamento_list')
    return render(request, 'anagrafiche/confirm_delete.html', {
        'active_nav': 'forme_pagamento',
        'object': fp,
        'object_label': str(fp),
        'urls': {'list': 'anagrafiche:forme_pagamento_list'},
    })


# ---------------------------------------------------------------------------
# AnagraficaAzienda (singleton)
# ---------------------------------------------------------------------------


@login_required
@require_perm('impostazioni.modifica')
def anagrafica_azienda_edit(request):
    """Edit singleton: si lavora sempre sull'unica istanza (creata se assente)."""
    az = AnagraficaAzienda.current()
    if request.method == 'POST':
        form = AnagraficaAziendaForm(request.POST, request.FILES, instance=az)
        if form.is_valid():
            form.save()
            messages.success(request, 'Anagrafica azienda aggiornata.')
            return redirect('anagrafiche:anagrafica_azienda')
    else:
        form = AnagraficaAziendaForm(instance=az)
    return render(request, 'anagrafiche/anagrafica_azienda.html', {
        'active_nav': 'anagrafica_azienda',
        'form': form,
        'azienda': az,
    })
