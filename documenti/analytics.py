"""
Endpoint analitici sui documenti.

Tutti leggono dai modelli esistenti (niente tabelle di staging). Le
aggregazioni sono volutamente semplici: sum/group_by in Python quando
l'SQL richiederebbe workarounds, SQL quando banale.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Case, DecimalField, F, Q, Sum, Value, When
from django.shortcuts import render

from accounts.permissions import require_perm

from .models import (
    RigaFatturaAcquisto,
    ScadenzaFattura,
    ScadenzaFatturaAcquisto,
    TestataFattura,
    TestataFatturaAcquisto,
)


@login_required
@require_perm('documenti.vedi')
def scadenziario_attivo(request):
    """Scadenze fatture cliente aperte (pagata=False). Aging buckets."""
    from django.utils.timezone import now

    oggi = now().date()
    qs = (
        ScadenzaFattura.objects
        .select_related('fattura__cliente')
        .filter(fattura__pagata=False)
        .order_by('data')
    )

    cliente_id = request.GET.get('cliente')
    if cliente_id and cliente_id.isdigit():
        qs = qs.filter(fattura__cliente_id=int(cliente_id))

    scadenze_rows = []
    buckets = {'scadute': Decimal('0'), 'in_scadenza_30': Decimal('0'),
               'oltre_30': Decimal('0'), 'totale': Decimal('0')}
    for s in qs:
        giorni = (s.data - oggi).days
        bucket = 'scadute' if giorni < 0 else ('in_scadenza_30' if giorni <= 30 else 'oltre_30')
        buckets[bucket] += s.importo
        buckets['totale'] += s.importo
        scadenze_rows.append({'scadenza': s, 'giorni': giorni, 'bucket': bucket})

    from clienti.models import Cliente
    ctx = {
        'active_nav': 'scadenziario',
        'scadenze_rows': scadenze_rows,
        'buckets': buckets,
        'oggi': oggi,
        'clienti': Cliente.objects.order_by('ragione_sociale'),
        'cliente_sel': cliente_id or '',
    }
    return render(request, 'documenti/analytics/scadenziario_attivo.html', ctx)


@login_required
@require_perm('documenti.vedi')
def scadenziario_passivo(request):
    """Scadenze fatture acquisto aperte (importo_residuo > 0)."""
    from django.utils.timezone import now

    oggi = now().date()
    qs = (
        ScadenzaFatturaAcquisto.objects
        .select_related('fattura__fornitore')
        .order_by('data_scadenza')
    )

    fornitore_id = request.GET.get('fornitore')
    if fornitore_id and fornitore_id.isdigit():
        qs = qs.filter(fattura__fornitore_id=int(fornitore_id))

    rows = []
    buckets = {'scadute': Decimal('0'), 'in_scadenza_30': Decimal('0'),
               'oltre_30': Decimal('0'), 'totale_residuo': Decimal('0')}
    for s in qs:
        residuo = s.importo_residuo
        if residuo <= 0:
            continue
        giorni = (s.data_scadenza - oggi).days
        bucket = 'scadute' if giorni < 0 else ('in_scadenza_30' if giorni <= 30 else 'oltre_30')
        buckets[bucket] += residuo
        buckets['totale_residuo'] += residuo
        rows.append({
            'scadenza': s,
            'giorni': giorni,
            'bucket': bucket,
            'residuo': residuo,
        })

    from anagrafiche.models import Fornitore
    ctx = {
        'active_nav': 'scadenziario',
        'rows': rows,
        'buckets': buckets,
        'oggi': oggi,
        'fornitori': Fornitore.objects.filter(soft_delete=False).order_by('ragione_sociale'),
        'fornitore_sel': fornitore_id or '',
    }
    return render(request, 'documenti/analytics/scadenziario_passivo.html', ctx)


@login_required
@require_perm('documenti.vedi')
def vendite_per_cliente(request):
    """Aggrega imponibile delle fatture cliente per cliente, con filtro anno."""
    from clienti.models import Cliente

    qs = TestataFattura.objects.select_related('cliente').exclude(cliente__isnull=True)

    anno = request.GET.get('anno')
    if anno and anno.isdigit():
        qs = qs.filter(anno=int(anno))

    agg: dict[int, dict] = {}
    for f in qs:
        cid = f.cliente_id
        slot = agg.setdefault(cid, {
            'cliente': f.cliente,
            'totale': Decimal('0'),
            'count': 0,
            'pagato': Decimal('0'),
        })
        slot['totale'] += f.imponibile or Decimal('0')
        slot['count'] += 1
        if f.pagata:
            slot['pagato'] += f.imponibile or Decimal('0')

    rows = sorted(agg.values(), key=lambda s: s['totale'], reverse=True)
    totale_complessivo = sum((s['totale'] for s in rows), Decimal('0'))
    for r in rows:
        r['pct'] = (r['totale'] / totale_complessivo * 100) if totale_complessivo else 0
        r['pagato_pct'] = (r['pagato'] / r['totale'] * 100) if r['totale'] else 0

    anni = list(
        TestataFattura.objects.values_list('anno', flat=True).distinct().order_by('-anno'),
    )
    return render(request, 'documenti/analytics/vendite_per_cliente.html', {
        'active_nav': 'vendite',
        'rows': rows,
        'totale_complessivo': totale_complessivo,
        'anno_sel': anno or '',
        'anni_choices': anni,
    })


@login_required
@require_perm('documenti.vedi')
def provvigioni_agenti(request):
    """Sintetizza provvigioni per agente: ``somma(imponibile_fattura * provvigione%)``."""
    from .models import AgenteFattura

    qs = AgenteFattura.objects.select_related(
        'agente', 'fattura', 'fattura__cliente',
    )
    anno = request.GET.get('anno')
    if anno and anno.isdigit():
        qs = qs.filter(fattura__anno=int(anno))

    agg: dict[int, dict] = {}
    for af in qs:
        aid = af.agente_id
        slot = agg.setdefault(aid, {
            'agente': af.agente,
            'fatture': [],
            'imponibile_totale': Decimal('0'),
            'provvigione_totale': Decimal('0'),
        })
        imp = af.fattura.imponibile or Decimal('0')
        prov = imp * Decimal(af.provvigione) / Decimal('100')
        slot['imponibile_totale'] += imp
        slot['provvigione_totale'] += prov
        slot['fatture'].append({
            'fattura': af.fattura,
            'imponibile': imp,
            'percentuale': af.provvigione,
            'provvigione': prov.quantize(Decimal('0.01')),
        })

    rows = sorted(agg.values(), key=lambda s: s['provvigione_totale'], reverse=True)
    anni = list(
        TestataFattura.objects.values_list('anno', flat=True).distinct().order_by('-anno'),
    )
    return render(request, 'documenti/analytics/provvigioni_agenti.html', {
        'active_nav': 'provvigioni',
        'rows': rows,
        'totale_provvigioni': sum((s['provvigione_totale'] for s in rows), Decimal('0')),
        'anno_sel': anno or '',
        'anni_choices': anni,
    })


@login_required
@require_perm('documenti.vedi')
def margine_articoli(request):
    """Calcola il margine sulle righe fattura cliente: imponibile − prezzo_acquisto*quantita.

    Considera solo righe collegate a fatture cliente per evitare di duplicare
    il dato (le stesse righe possono essere replicate dal workflow su offerta
    e ordine; la fattura è l'evento di valore monetario realizzato).
    """
    from .models import RigaDocumento

    qs = RigaDocumento.objects.select_related(
        'articolo', 'testata_fattura__cliente',
    ).filter(testata_fattura__isnull=False)

    anno = request.GET.get('anno')
    if anno and anno.isdigit():
        qs = qs.filter(testata_fattura__anno=int(anno))

    agg: dict[int | None, dict] = {}
    totale_ricavi = Decimal('0')
    totale_costi = Decimal('0')
    for r in qs:
        ricavo = r.imponibile
        costo = (r.prezzo_acquisto or Decimal('0')) * r.quantita
        totale_ricavi += ricavo
        totale_costi += costo
        articolo_key = r.articolo_id
        slot = agg.setdefault(articolo_key, {
            'articolo': r.articolo,
            'descrizione_libera_set': set(),
            'quantita': Decimal('0'),
            'ricavi': Decimal('0'),
            'costi': Decimal('0'),
            'count': 0,
        })
        if not r.articolo and r.descrizione_libera:
            slot['descrizione_libera_set'].add(r.descrizione_libera[:60])
        slot['quantita'] += r.quantita
        slot['ricavi'] += ricavo
        slot['costi'] += costo
        slot['count'] += 1

    rows = []
    for slot in agg.values():
        margine = slot['ricavi'] - slot['costi']
        rows.append({
            'articolo': slot['articolo'],
            'label_libera': ', '.join(sorted(slot['descrizione_libera_set']))[:80] if not slot['articolo'] else '',
            'count': slot['count'],
            'quantita': slot['quantita'],
            'ricavi': slot['ricavi'].quantize(Decimal('0.01')),
            'costi': slot['costi'].quantize(Decimal('0.01')),
            'margine': margine.quantize(Decimal('0.01')),
            'margine_pct': (margine / slot['ricavi'] * 100) if slot['ricavi'] else 0,
        })
    rows.sort(key=lambda s: s['margine'], reverse=True)

    anni = list(
        TestataFattura.objects.values_list('anno', flat=True).distinct().order_by('-anno'),
    )
    return render(request, 'documenti/analytics/margine_articoli.html', {
        'active_nav': 'margine',
        'rows': rows,
        'totale_ricavi': totale_ricavi.quantize(Decimal('0.01')),
        'totale_costi': totale_costi.quantize(Decimal('0.01')),
        'totale_margine': (totale_ricavi - totale_costi).quantize(Decimal('0.01')),
        'margine_pct': ((totale_ricavi - totale_costi) / totale_ricavi * 100) if totale_ricavi else 0,
        'anno_sel': anno or '',
        'anni_choices': anni,
    })


@login_required
@require_perm('documenti.vedi')
def top_articoli(request):
    """Top articoli per quantità venduta e ricavi (sulle fatture cliente).

    Considera solo righe collegate a ``TestataFattura`` (le stesse righe
    possono essere replicate da workflow su offerte e ordini, e li
    escluderemmo per non contare due volte). Le righe senza articolo
    valorizzato vengono raggruppate per ``descrizione_libera`` come fallback.
    """
    from .models import RigaDocumento

    qs = RigaDocumento.objects.select_related('articolo').filter(
        testata_fattura__isnull=False,
    )
    anno = request.GET.get('anno')
    if anno and anno.isdigit():
        qs = qs.filter(testata_fattura__anno=int(anno))

    sort = request.GET.get('sort', 'ricavi')

    agg: dict = {}
    for r in qs:
        if r.articolo_id:
            key = ('art', r.articolo_id)
            label = f'{r.articolo.codice} — {r.articolo.descrizione or ""}'
        else:
            label_libera = (r.descrizione_libera or '—').strip()[:80]
            key = ('lib', label_libera.lower())
            label = label_libera or '— senza articolo —'
        slot = agg.setdefault(key, {
            'label': label,
            'articolo': r.articolo,
            'quantita': Decimal('0'),
            'ricavi': Decimal('0'),
            'count': 0,
        })
        slot['quantita'] += r.quantita
        slot['ricavi'] += r.imponibile
        slot['count'] += 1

    rows = list(agg.values())
    if sort == 'quantita':
        rows.sort(key=lambda s: s['quantita'], reverse=True)
    else:
        rows.sort(key=lambda s: s['ricavi'], reverse=True)

    totale_ricavi = sum((r['ricavi'] for r in rows), Decimal('0'))
    for r in rows:
        r['ricavi'] = r['ricavi'].quantize(Decimal('0.01'))
        r['pct'] = float(r['ricavi'] / totale_ricavi * 100) if totale_ricavi else 0

    anni = list(
        TestataFattura.objects.values_list('anno', flat=True)
        .distinct().order_by('-anno'),
    )
    return render(request, 'documenti/analytics/top_articoli.html', {
        'active_nav': 'top_articoli',
        'rows': rows[:50],
        'totale_ricavi': totale_ricavi.quantize(Decimal('0.01')),
        'anno_sel': anno or '',
        'anni_choices': anni,
        'sort': sort,
    })


@login_required
@require_perm('documenti.vedi')
def costi_per_categoria(request):
    """Somma imponibile RigaFatturaAcquisto raggruppato per CategoriaCosto.

    Filtri opzionali: ``anno``, ``fornitore``.
    """
    from anagrafiche.models import CategoriaCosto, Fornitore

    qs = RigaFatturaAcquisto.objects.select_related(
        'categoria_costo', 'testata__fornitore',
    ).filter(testata__stato=TestataFatturaAcquisto.StatoFattura.CONFERMATA)

    anno = request.GET.get('anno')
    if anno and anno.isdigit():
        qs = qs.filter(testata__anno=int(anno))

    fornitore_id = request.GET.get('fornitore')
    if fornitore_id and fornitore_id.isdigit():
        qs = qs.filter(testata__fornitore_id=int(fornitore_id))

    # Aggrego in Python (imponibile è @property, non posso SUM nativo)
    agg: dict[int | None, dict] = {}
    for r in qs:
        cat = r.categoria_costo
        key = cat.pk if cat else None
        slot = agg.setdefault(key, {
            'codice': cat.codice if cat else '—',
            'descrizione': cat.descrizione if cat else 'Non categorizzati',
            'totale': Decimal('0'),
            'count': 0,
        })
        slot['totale'] += r.imponibile
        slot['count'] += 1

    totale_complessivo = sum((s['totale'] for s in agg.values()), Decimal('0'))
    rows = sorted(agg.values(), key=lambda s: s['totale'], reverse=True)
    # Percentuali
    for r in rows:
        r['pct'] = (r['totale'] / totale_complessivo * 100) if totale_complessivo else 0

    anni = list(
        TestataFatturaAcquisto.objects.values_list('anno', flat=True)
        .distinct().order_by('-anno')
    )
    ctx = {
        'active_nav': 'costi',
        'rows': rows,
        'totale_complessivo': totale_complessivo,
        'anno_sel': anno or '',
        'anni_choices': anni,
        'fornitore_sel': fornitore_id or '',
        'fornitori': Fornitore.objects.filter(soft_delete=False).order_by('ragione_sociale'),
    }
    return render(request, 'documenti/analytics/costi_per_categoria.html', ctx)
