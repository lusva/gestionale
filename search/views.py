"""Ricerca globale cross-model.

Un singolo endpoint `/cerca/?q=...` che interroga Cliente, Contatto,
Opportunita e Attivita.

Modalità:
- Default (panoramica): primi 10 risultati per ogni gruppo + conteggio
  totale per gruppo.
- Focus su un gruppo: `?group=<kind>&page=N` paginare all'interno del
  gruppo con 20 risultati per pagina.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, Paginator
from django.db.models import Q
from django.shortcuts import render

from attivita.models import Attivita
from clienti.models import Cliente, Contatto
from opportunita.models import Opportunita

# Documenti: import lazy per non rompere se l'app è disabilitata
try:
    from documenti.models import (
        TestataDdt,
        TestataFattura,
        TestataNotaCredito,
        TestataOfferta,
        TestataOrdine,
    )
    _DOCUMENTI_AVAILABLE = True
except Exception:
    _DOCUMENTI_AVAILABLE = False


OVERVIEW_CAP = 10
GROUP_PAGE_SIZE = 20


def _querysets(q: str):
    """Ritorna i queryset *non tagliati* per ogni gruppo (ordinati)."""
    if not q:
        return {}
    out = {
        'clienti': (
            Cliente.objects.filter(
                Q(ragione_sociale__icontains=q)
                | Q(partita_iva__icontains=q)
                | Q(pec__icontains=q)
                | Q(citta__icontains=q),
            ).select_related('settore').order_by('ragione_sociale')
        ),
        'contatti': (
            Contatto.objects.filter(
                Q(nome__icontains=q) | Q(cognome__icontains=q)
                | Q(email__icontains=q) | Q(telefono__icontains=q),
            ).select_related('cliente').order_by('-primary', 'cognome')
        ),
        'opportunita': (
            Opportunita.objects.filter(
                Q(titolo__icontains=q) | Q(descrizione__icontains=q)
                | Q(cliente__ragione_sociale__icontains=q),
            ).select_related('cliente').order_by('-updated_at')
        ),
        'attivita': (
            Attivita.objects.filter(
                Q(titolo__icontains=q) | Q(descrizione__icontains=q),
            ).select_related('cliente').order_by('-data')
        ),
    }
    if _DOCUMENTI_AVAILABLE:
        # Aggrego risultati documenti in un'unica lista (non un queryset)
        # perché vengono da modelli diversi. Tagliato successivamente in
        # base alla modalità (overview/focus).
        documenti = _search_documenti(q)
        out['documenti'] = documenti
    return out


def _search_documenti(q: str):
    """Cerca su tutte e 5 le testate ciclo attivo: ritorna lista di dict
    omogenei ``{tipo, numero, anno, data, cliente, totale, url, sezionale}``
    ordinati per data desc.
    """
    from django.urls import reverse

    rows = []
    cond = (
        Q(numero__icontains=q)
        | Q(cliente__ragione_sociale__icontains=q)
        | Q(cliente__partita_iva__icontains=q)
        | Q(note__icontains=q)
    )

    docs_meta = [
        ('Fattura', TestataFattura, 'documenti:fattura_detail'),
        ('Offerta', TestataOfferta, 'documenti:offerta_detail'),
        ('Ordine', TestataOrdine, 'documenti:ordine_detail'),
        ('DDT', TestataDdt, 'documenti:ddt_detail'),
        ('Nota credito', TestataNotaCredito, 'documenti:nota_credito_detail'),
    ]
    for tipo, model, url_name in docs_meta:
        for d in model.objects.filter(cond).select_related('cliente')[:50]:
            rows.append({
                'tipo': tipo,
                'numero': d.numero, 'anno': d.anno,
                'sezionale': getattr(d, 'sezionale', '') or '',
                'data': d.data_documento,
                'cliente': d.cliente,
                'totale': d.imponibile,
                'url': reverse(url_name, args=[d.pk]),
                '_pk': d.pk,
            })
    rows.sort(key=lambda r: (r['anno'], r['numero']), reverse=True)
    return rows


GROUP_META = {
    'clienti':     ('Clienti',     'users'),
    'contatti':    ('Contatti',    'user'),
    'opportunita': ('Opportunità', 'target'),
    'attivita':    ('Attività',    'calendar'),
    'documenti':   ('Documenti',   'doc'),
}


@login_required
def cerca(request):
    q = (request.GET.get('q') or '').strip()
    group = (request.GET.get('group') or '').strip()
    if group and group not in GROUP_META:
        group = ''

    qsets = _querysets(q)

    ctx = {
        'q': q,
        'active_nav': 'ricerca',
        'group_meta': GROUP_META,
    }

    if q and group:
        # Vista focus: paginazione completa sul singolo gruppo
        qs = qsets[group]
        paginator = Paginator(qs, GROUP_PAGE_SIZE)
        try:
            page_obj = paginator.page(request.GET.get('page') or 1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages or 1)
        label, icon_name = GROUP_META[group]
        ctx.update({
            'mode': 'focus',
            'focus_group': group,
            'focus_label': label,
            'focus_icon': icon_name,
            'items': page_obj.object_list,
            'paginator': paginator,
            'page_obj': page_obj,
            'total_focus': paginator.count,
        })
    else:
        # Vista panoramica: cap 10 per gruppo + conteggi
        groups = []
        total = 0
        for key, (label, icon_name) in GROUP_META.items():
            qs = qsets.get(key)
            if qs is None:
                items, count = [], 0
            else:
                # ``documenti`` è una lista in memoria (modelli misti).
                count = qs.count() if hasattr(qs, 'count') and not isinstance(qs, list) else len(qs)
                items = list(qs[:OVERVIEW_CAP])
            total += count
            groups.append({
                'kind': key, 'label': label, 'icon': icon_name,
                'items': items, 'count': count,
                'overflow': max(0, count - OVERVIEW_CAP),
            })
        ctx.update({
            'mode': 'overview',
            'groups': groups,
            'total': total,
        })
    return render(request, 'search/results.html', ctx)
