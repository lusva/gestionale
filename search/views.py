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

from accounts.permissions import has_perm
from attivita.models import Attivita
from clienti.models import Cliente, Contatto
from clienti.ricerca import CAMPI_CLIENTE, CAMPI_CONTATTO, filtro_parole, normalizza_query
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


def _querysets(q: str, user):
    """Ritorna i queryset *non tagliati* per ogni gruppo (ordinati)."""
    if not q:
        return {}
    out = {
        'opportunita': (
            Opportunita.objects.filter(filtro_parole(
                q, 'titolo', 'descrizione', 'cliente__ragione_sociale',
            )).select_related('cliente').order_by('-updated_at', '-pk')
        ),
        'attivita': (
            Attivita.objects.filter(filtro_parole(
                q, 'titolo', 'descrizione', 'cliente__ragione_sociale',
            )).select_related('cliente').order_by('-data', '-pk')
        ),
    }
    if has_perm(user, 'clienti.vedi'):
        out['clienti'] = Cliente.objects.filter(filtro_parole(
            q, *CAMPI_CLIENTE,
        )).select_related('settore').distinct().order_by('ragione_sociale', 'pk')
        out['contatti'] = Contatto.objects.filter(filtro_parole(
            q, *CAMPI_CONTATTO,
        )).select_related('cliente').order_by('-primary', 'cognome', 'nome', 'pk')
    if _DOCUMENTI_AVAILABLE and has_perm(user, 'documenti.vedi'):
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
        cond = _filtro_numero(q)
        if cond is None:
            cond = filtro_parole(
                q, 'cliente__ragione_sociale', 'cliente__partita_iva', 'note',
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
        rows.sort(key=lambda r: (r['anno'], r['numero'], r['tipo'], r['_pk']), reverse=True)
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
    q = normalizza_query(request.GET.get('q'))
    group = (request.GET.get('group') or '').strip()
    if group and group not in GROUP_META:
        group = ''

    qsets = _querysets(q, request.user)
    if group not in qsets:
        group = ''

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


# ---------------------------------------------------------------------------
# Ricerca rapida per la palette comandi (⌘K): pochi risultati per gruppo, in
# JSON, mentre si scrive. La pagina /cerca/ resta la ricerca completa.
# ---------------------------------------------------------------------------

RAPIDA_PER_GRUPPO = 5


def _numero_anno(q: str):
    """«12», «12/2026» o «12-2026» → (12, 2026|None); altrimenti None."""
    import re
    m = re.fullmatch(r'\s*(\d{1,7})\s*(?:[/\-]\s*(\d{4}))?\s*', q)
    if not m:
        return None
    return int(m.group(1)), (int(m.group(2)) if m.group(2) else None)


def _filtro_numero(q: str):
    na = _numero_anno(q)
    if na is None:
        return None
    filtro = Q(numero=na[0])
    if na[1]:
        filtro &= Q(anno=na[1])
    return filtro


@login_required
def rapida(request):
    from django.http import JsonResponse
    from django.urls import reverse

    from core_settings.templatetags.ui import euro

    q = normalizza_query(request.GET.get('q'))
    gruppi = []
    if len(q) < 2 and _numero_anno(q) is None:
        return JsonResponse({'gruppi': gruppi})
    n = RAPIDA_PER_GRUPPO
    numero = _filtro_numero(q)
    vede_doc = _DOCUMENTI_AVAILABLE and has_perm(request.user, 'documenti.vedi')

    if vede_doc:
        filtro = filtro_parole(q, 'cliente__ragione_sociale', 'cliente__partita_iva', 'note')
        if numero is not None:
            filtro = numero
        fatture = (TestataFattura.objects.filter(filtro).select_related('cliente')
                   .order_by('-anno', '-numero')[:n])
        gruppi.append({'nome': 'Fatture', 'voci': [{
            'titolo': f'Fattura {f.sezionale + "/" if f.sezionale else ""}{f.numero}/{f.anno}',
            'sotto': ' · '.join(x for x in (
                f.cliente.ragione_sociale if f.cliente else '', euro(f.imponibile, 2),
                'pagata' if f.pagata else 'da incassare') if x),
            'url': reverse('documenti:fattura_detail', args=[f.pk]),
        } for f in fatture]})

        from documenti.models import TestataFatturaAcquisto
        filtro = filtro_parole(
            q, 'fornitore__ragione_sociale', 'fornitore__partita_iva', 'numero_fornitore',
        )
        if numero is not None:
            filtro |= numero
        acquisti = (TestataFatturaAcquisto.objects.filter(filtro).select_related('fornitore')
                    .order_by('-anno', '-numero')[:n])
        gruppi.append({'nome': 'Fatture di acquisto', 'voci': [{
            'titolo': f'Acquisto {a.numero}/{a.anno}'
                      + (f' · rif. {a.numero_fornitore}' if a.numero_fornitore else ''),
            'sotto': ' · '.join(x for x in (
                a.fornitore.ragione_sociale if a.fornitore else '', euro(a.totale_documento, 2),
                'pagata' if a.pagata else 'da pagare') if x),
            'url': reverse('documenti:fattura_acquisto_detail', args=[a.pk]),
        } for a in acquisti]})

        altri = []
        for model, nome, url_name in ((TestataOfferta, 'Offerta', 'documenti:offerta_detail'),
                                      (TestataOrdine, 'Ordine', 'documenti:ordine_detail'),
                                      (TestataDdt, 'DDT', 'documenti:ddt_detail'),
                                      (TestataNotaCredito, 'Nota di credito',
                                       'documenti:nota_credito_detail')):
            filtro = numero if numero is not None else filtro_parole(
                q, 'cliente__ragione_sociale', 'cliente__partita_iva', 'note',
            )
            for d in model.objects.filter(filtro).select_related('cliente').order_by('-anno', '-numero')[:3]:
                altri.append({
                    'titolo': f'{nome} {d.numero}/{d.anno}',
                    'sotto': d.cliente.ragione_sociale if d.cliente else '',
                    'url': reverse(url_name, args=[d.pk]),
                })
        gruppi.append({'nome': 'Altri documenti', 'voci': altri[:n]})

    if has_perm(request.user, 'clienti.vedi'):
        clienti = (Cliente.objects.filter(filtro_parole(
            q, *CAMPI_CLIENTE,
        )).distinct().order_by('ragione_sociale', 'pk')[:n])
        gruppi.append({'nome': 'Clienti', 'voci': [{
            'titolo': c.ragione_sociale,
            'sotto': ' · '.join(x for x in (c.partita_iva, c.citta) if x),
            'url': reverse('clienti:detail', args=[c.pk]),
        } for c in clienti]})
        contatti = (Contatto.objects.filter(filtro_parole(
            q, *CAMPI_CONTATTO,
        )).select_related('cliente').order_by('cognome', 'nome', 'pk')[:3])
        gruppi.append({'nome': 'Contatti', 'voci': [{
            'titolo': f'{c.nome} {c.cognome}'.strip(),
            'sotto': ' · '.join(x for x in (c.cliente.ragione_sociale, c.email) if x),
            'url': reverse('clienti:detail', args=[c.cliente_id]),
        } for c in contatti]})

    if vede_doc:
        from anagrafiche.models import Articolo, Fornitore
        fornitori = (Fornitore.objects.filter(soft_delete=False).filter(
            Q(ragione_sociale__icontains=q) | Q(partita_iva__icontains=q)
            | Q(cognome__icontains=q)).order_by('ragione_sociale')[:n])
        gruppi.append({'nome': 'Fornitori', 'voci': [{
            'titolo': str(f),
            'sotto': f.partita_iva or '',
            'url': reverse('anagrafiche:fornitore_detail', args=[f.pk]),
        } for f in fornitori]})
        articoli = (Articolo.objects.filter(Q(codice__icontains=q) | Q(descrizione__icontains=q))
                    .order_by('codice')[:n])
        gruppi.append({'nome': 'Articoli', 'voci': [{
            'titolo': f'{a.codice} — {a.descrizione}' if a.descrizione else a.codice,
            'sotto': euro(a.prezzo_listino, 2) if a.prezzo_listino else '',
            'url': reverse('anagrafiche:articolo_detail', args=[a.pk]),
        } for a in articoli]})

    opportunita = (Opportunita.objects.filter(filtro_parole(
        q, 'titolo', 'descrizione', 'cliente__ragione_sociale',
    )).select_related('cliente').order_by('-updated_at', '-pk')[:3])
    gruppi.append({'nome': 'Opportunità', 'voci': [{
        'titolo': o.titolo,
        'sotto': o.cliente.ragione_sociale if o.cliente else '',
        'url': reverse('opportunita:detail', args=[o.pk]),
    } for o in opportunita]})

    attivita = (Attivita.objects.filter(filtro_parole(
        q, 'titolo', 'descrizione', 'cliente__ragione_sociale',
    )).select_related('cliente').order_by('completata', '-data', '-pk')[:3])
    gruppi.append({'nome': 'Attività', 'voci': [{
        'titolo': a.titolo,
        'sotto': ' · '.join(x for x in (
            a.cliente.ragione_sociale if a.cliente else '',
            'completata' if a.completata else 'da completare',
        ) if x),
        'url': reverse('attivita:detail', args=[a.pk]),
    } for a in attivita]})

    return JsonResponse({'gruppi': [g for g in gruppi if g['voci']]})
