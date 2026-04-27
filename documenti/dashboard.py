"""Dashboard riassuntiva del modulo documenti.

Aggrega in una sola pagina KPI di fatturato (mese/anno corrente vs anno
precedente), AR/AP aging stretto, top clienti, costi per categoria del
mese, e attività recente sui documenti.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render
from django.utils.timezone import now

from accounts.permissions import require_perm

from .models import (
    RigaFatturaAcquisto,
    ScadenzaFattura,
    ScadenzaFatturaAcquisto,
    TestataFattura,
    TestataFatturaAcquisto,
)


def _sum(qs, field='imponibile') -> Decimal:
    total = Decimal('0')
    for v in qs.values_list(field, flat=True):
        if v is not None:
            total += v
    return total


@login_required
@require_perm('documenti.vedi')
def dashboard(request):
    today = now().date()
    inizio_mese = today.replace(day=1)
    if inizio_mese.month == 1:
        prev_mese_inizio = inizio_mese.replace(year=inizio_mese.year - 1, month=12)
    else:
        prev_mese_inizio = inizio_mese.replace(month=inizio_mese.month - 1)
    inizio_anno = today.replace(month=1, day=1)
    prev_anno_inizio = inizio_anno.replace(year=inizio_anno.year - 1)

    # === KPI fatturato (ciclo attivo) ===
    fatt_mese = _sum(TestataFattura.objects.filter(
        data_documento__gte=inizio_mese, data_documento__lte=today,
    ))
    fatt_mese_prec = _sum(TestataFattura.objects.filter(
        data_documento__gte=prev_mese_inizio, data_documento__lt=inizio_mese,
    ))
    fatt_anno = _sum(TestataFattura.objects.filter(
        data_documento__gte=inizio_anno, data_documento__lte=today,
    ))
    fatt_anno_prec = _sum(TestataFattura.objects.filter(
        data_documento__gte=prev_anno_inizio, data_documento__lt=inizio_anno,
    ))

    delta_mese_pct = None
    if fatt_mese_prec:
        delta_mese_pct = (fatt_mese - fatt_mese_prec) / fatt_mese_prec * 100
    delta_anno_pct = None
    if fatt_anno_prec:
        delta_anno_pct = (fatt_anno - fatt_anno_prec) / fatt_anno_prec * 100

    # === AR aging (clienti) ===
    ar_scaduto = Decimal('0')
    ar_in_scadenza_30 = Decimal('0')
    for s in ScadenzaFattura.objects.select_related('fattura').filter(
        fattura__pagata=False,
    ):
        giorni = (s.data - today).days
        if giorni < 0:
            ar_scaduto += s.importo
        elif giorni <= 30:
            ar_in_scadenza_30 += s.importo

    # === AP aging (fornitori) ===
    ap_scaduto = Decimal('0')
    ap_in_scadenza_30 = Decimal('0')
    for s in ScadenzaFatturaAcquisto.objects.select_related('fattura'):
        residuo = s.importo_residuo
        if residuo <= 0:
            continue
        giorni = (s.data_scadenza - today).days
        if giorni < 0:
            ap_scaduto += residuo
        elif giorni <= 30:
            ap_in_scadenza_30 += residuo

    # === Costi del mese (riga acquisto su fatture confermate) ===
    costi_mese = Decimal('0')
    for r in RigaFatturaAcquisto.objects.select_related('testata').filter(
        testata__stato=TestataFatturaAcquisto.StatoFattura.CONFERMATA,
        testata__data_documento__gte=inizio_mese,
        testata__data_documento__lte=today,
    ):
        costi_mese += r.imponibile

    # === Top 5 clienti (anno corrente) ===
    top_clienti_agg: dict[int, dict] = {}
    for f in TestataFattura.objects.select_related('cliente').filter(
        data_documento__gte=inizio_anno,
        data_documento__lte=today,
    ).exclude(cliente__isnull=True):
        slot = top_clienti_agg.setdefault(f.cliente_id, {
            'cliente': f.cliente, 'totale': Decimal('0'), 'count': 0,
        })
        slot['totale'] += f.imponibile or Decimal('0')
        slot['count'] += 1
    top_clienti = sorted(
        top_clienti_agg.values(), key=lambda s: s['totale'], reverse=True,
    )[:5]

    # === Documenti recenti (ultimi 8) ===
    fatture_recenti = TestataFattura.objects.select_related('cliente').order_by(
        '-created_at',
    )[:8]

    # === Mini barchart fatturato ultimi 6 mesi ===
    barchart = []
    for offset in range(5, -1, -1):
        m_year = inizio_mese.year
        m_month = inizio_mese.month - offset
        while m_month <= 0:
            m_month += 12
            m_year -= 1
        from calendar import monthrange
        last_day = monthrange(m_year, m_month)[1]
        ms = date(m_year, m_month, 1)
        me = date(m_year, m_month, last_day)
        valore = _sum(TestataFattura.objects.filter(
            data_documento__gte=ms, data_documento__lte=me,
        ))
        barchart.append({'mese': ms.strftime('%b'), 'anno': m_year, 'valore': valore})
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
