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
from django.core.paginator import Paginator
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
        # Risultati documenti da modelli diversi: oggetto lazy con count()
        # e slicing, valutato solo per la finestra richiesta.
        documenti = _search_documenti(q)
        out['documenti'] = documenti
    return out


class _DocumentiResults:
    """Risultati documenti su 5 modelli diversi, valutati in modo lazy.

    Espone ``count()`` (somma dei COUNT per modello) e lo slicing, così da
    essere usabile sia nella panoramica sia con ``Paginator``. Lo slice
    ``[a:b]`` carica al massimo ``b`` righe per modello (ordinate per
    anno/numero desc), le fonde e restituisce la finestra richiesta.
    """

    ordered = True

    def __init__(self, q: str):
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
        self._sources = [
            (
                tipo,
                model.objects.filter(cond)
                .select_related('cliente').order_by('-anno', '-numero', '-pk'),
                url_name,
            )
            for tipo, model, url_name in docs_meta
        ]
        self._count = None

    def count(self):
        if self._count is None:
            self._count = sum(qs.count() for _, qs, _ in self._sources)
        return self._count

    def __len__(self):
        return self.count()

    def _rows(self, limit):
        from django.urls import reverse

        rows = []
        for tipo, qs, url_name in self._sources:
            for d in qs[:limit]:
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

    def __getitem__(self, key):
        if isinstance(key, slice):
            start = key.start or 0
            stop = key.stop if key.stop is not None else self.count()
            return self._rows(stop)[start:stop]
        return self._rows(key + 1)[key]


def _search_documenti(q: str):
    """Cerca su tutte e 5 le testate ciclo attivo. Ritorna un oggetto lazy
    (``count()`` + slicing) i cui elementi sono dict omogenei
    ``{tipo, numero, anno, data, cliente, totale, url, sezionale}``
    ordinati per anno/numero desc.
    """
    return _DocumentiResults(q)


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
        page_obj = paginator.get_page(request.GET.get('page'))
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
                count = qs.count()
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
