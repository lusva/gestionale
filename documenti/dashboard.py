"""Dashboard riassuntiva del modulo documenti.

Aggrega in una sola pagina KPI di fatturato (mese/anno corrente vs anno
precedente), AR/AP aging stretto, top clienti, costi per categoria del
mese, e attività recente sui documenti.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.shortcuts import render
from django.utils.timezone import localdate

from accounts.permissions import require_perm
from clienti.models import Cliente

from .models import (
    RigaFatturaAcquisto,
    ScadenzaFattura,
    TestataFattura,
    TestataFatturaAcquisto,
    scadenze_acquisto_con_residuo,
)


_MESI = ('gen', 'feb', 'mar', 'apr', 'mag', 'giu',
         'lug', 'ago', 'set', 'ott', 'nov', 'dic')


def _d(v) -> Decimal:
    """Somma dal database (None se non ci sono righe) come Decimal al centesimo."""
    return Decimal(v or 0).quantize(Decimal('0.01'))


@login_required
@require_perm('documenti.vedi')
def dashboard(request):
    today = localdate()
    inizio_mese = today.replace(day=1)
    if inizio_mese.month == 1:
        prev_mese_inizio = inizio_mese.replace(year=inizio_mese.year - 1, month=12)
    else:
        prev_mese_inizio = inizio_mese.replace(month=inizio_mese.month - 1)
    inizio_anno = today.replace(month=1, day=1)
    prev_anno_inizio = inizio_anno.replace(year=inizio_anno.year - 1)
    # primo dei 6 mesi del grafico
    m_year, m_month = inizio_mese.year, inizio_mese.month - 5
    while m_month <= 0:
        m_month += 12
        m_year -= 1
    inizio_grafico = date(m_year, m_month, 1)

    # === KPI fatturato (ciclo attivo): una query ===
    imp = 'imponibile'
    fatt = TestataFattura.objects.aggregate(
        mese=Sum(imp, filter=Q(data_documento__gte=inizio_mese, data_documento__lte=today)),
        mese_prec=Sum(imp, filter=Q(data_documento__gte=prev_mese_inizio,
                                    data_documento__lt=inizio_mese)),
        anno=Sum(imp, filter=Q(data_documento__gte=inizio_anno, data_documento__lte=today)),
        anno_prec=Sum(imp, filter=Q(data_documento__gte=prev_anno_inizio,
                                    data_documento__lt=inizio_anno)),
    )
    fatt_mese, fatt_mese_prec = _d(fatt['mese']), _d(fatt['mese_prec'])
    fatt_anno, fatt_anno_prec = _d(fatt['anno']), _d(fatt['anno_prec'])

    delta_mese_pct = None
    if fatt_mese_prec:
        delta_mese_pct = (fatt_mese - fatt_mese_prec) / fatt_mese_prec * 100
    delta_anno_pct = None
    if fatt_anno_prec:
        delta_anno_pct = (fatt_anno - fatt_anno_prec) / fatt_anno_prec * 100

    # === AR aging (clienti) ===
    fra_30 = today + timedelta(days=30)
    from .models import crediti_aperti
    ar = crediti_aperti(ScadenzaFattura.objects.all()).aggregate(
        scaduto=Sum('residuo_db', filter=Q(data__lt=today)),
        in_30=Sum('residuo_db', filter=Q(data__gte=today, data__lte=fra_30)),
    )
    ar_scaduto, ar_in_scadenza_30 = _d(ar['scaduto']), _d(ar['in_30'])

    # === AP aging (fornitori): residuo calcolato nel database ===
    ap = scadenze_acquisto_con_residuo().filter(residuo_db__gt=0).aggregate(
        scaduto=Sum('residuo_db', filter=Q(data_scadenza__lt=today)),
        in_30=Sum('residuo_db', filter=Q(data_scadenza__gte=today, data_scadenza__lte=fra_30)),
    )
    ap_scaduto, ap_in_scadenza_30 = _d(ap['scaduto']), _d(ap['in_30'])

    # === Costi del mese (riga acquisto su fatture confermate) ===
    costi_mese = Decimal('0')
    for r in RigaFatturaAcquisto.objects.filter(
        testata__stato=TestataFatturaAcquisto.StatoFattura.CONFERMATA,
        testata__data_documento__gte=inizio_mese,
        testata__data_documento__lte=today,
    ):
        costi_mese += r.imponibile

    # === Top 5 clienti (anno corrente) ===
    righe_top = list(TestataFattura.objects.filter(
        data_documento__gte=inizio_anno, data_documento__lte=today, cliente__isnull=False,
    ).values('cliente').annotate(totale=Sum(imp), count=Count('pk')).order_by('-totale')[:5])
    clienti = Cliente.objects.in_bulk([r['cliente'] for r in righe_top])
    top_clienti = [{'cliente': clienti[r['cliente']], 'totale': _d(r['totale']), 'count': r['count']}
                   for r in righe_top]

    # === Documenti recenti (ultimi 8) ===
    fatture_recenti = TestataFattura.objects.select_related('cliente').order_by(
        '-created_at',
    )[:8]

    # === Mini barchart fatturato ultimi 6 mesi: una query raggruppata ===
    per_mese = {(r['m'].year, r['m'].month): r['v'] for r in TestataFattura.objects.filter(
        data_documento__gte=inizio_grafico, data_documento__lte=today,
    ).annotate(m=TruncMonth('data_documento')).values('m').annotate(v=Sum(imp)).order_by()}
    barchart = []
    for offset in range(5, -1, -1):
        m_year = inizio_mese.year
        m_month = inizio_mese.month - offset
        while m_month <= 0:
            m_month += 12
            m_year -= 1
        valore = _d(per_mese.get((m_year, m_month)))
        barchart.append({'mese': _MESI[m_month - 1], 'anno': m_year, 'valore': valore})
    max_bar = max((b['valore'] for b in barchart), default=Decimal('0')) or Decimal('1')
    for b in barchart:
        b['pct'] = float(b['valore'] / max_bar * 100) if max_bar else 0

    return render(request, 'documenti/dashboard.html', {
        'active_nav': 'dashboard_doc',
        'today': today,
        'fatt_mese': fatt_mese,
        'fatt_mese_prec': fatt_mese_prec,
        'delta_mese_pct': delta_mese_pct,
        'fatt_anno': fatt_anno,
        'fatt_anno_prec': fatt_anno_prec,
        'delta_anno_pct': delta_anno_pct,
        'ar_scaduto': ar_scaduto,
        'ar_in_scadenza_30': ar_in_scadenza_30,
        'ap_scaduto': ap_scaduto,
        'ap_in_scadenza_30': ap_in_scadenza_30,
        'costi_mese': costi_mese,
        'top_clienti': top_clienti,
        'fatture_recenti': fatture_recenti,
        'barchart': barchart,
    })
