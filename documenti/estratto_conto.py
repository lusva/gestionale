"""Estratto conto di un cliente: fatture con totale, scadenza e incasso, e i
totali che servono a chi deve sollecitare o rispondere al telefono
(fatturato, incassato, da incassare, scaduto). Gli stessi conti di PDF e
FatturaPA (``calcola_valori``), con una query per le righe di tutte le fatture.
Gli incassi parziali contano: da incassare e scaduto sono i residui.
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum
from django.utils.timezone import localdate

from .models import (
    RigaDocumento, ScadenzaFattura, TestataFattura, TestataNotaCredito, TestataOfferta, con_incassi,
    crediti_aperti,
)
from .utils import azienda_per_calcoli, calcola_valori


def estratto_conto(cliente, limite: int = 50) -> dict:
    oggi = localdate()
    fatture = list(con_incassi(TestataFattura.objects.filter(cliente=cliente))
                   .order_by('-anno', '-numero'))
    righe: dict[int, list] = {}
    for r in RigaDocumento.objects.filter(testata_fattura__cliente=cliente).select_related('iva'):
        righe.setdefault(r.testata_fattura_id, []).append(r)
    azienda = azienda_per_calcoli()
    fatturato = incassato = da_incassare = Decimal('0')
    for f in fatture:
        f.totale_calc = calcola_valori(f, righe.get(f.pk, []), azienda=azienda)['totale']
        fatturato += f.totale_calc
        if f.pagata:
            incassato += f.totale_calc
        else:
            incassato += f.incassato_tot
            da_incassare += f.totale_calc - f.incassato_tot
        f.giorni_scadenza = (f.prima_scadenza - oggi).days if f.prima_scadenza else None
        f.giorni_ritardo = -f.giorni_scadenza if f.giorni_scadenza else 0
    scaduto = crediti_aperti(ScadenzaFattura.objects.filter(
        fattura__cliente=cliente, data__lt=oggi)).aggregate(t=Sum('residuo_db'))['t'] or Decimal('0')
    note = list(TestataNotaCredito.objects.filter(cliente=cliente).order_by('-anno', '-numero')[:limite])
    for nc in note:
        nc.totale_calc = calcola_valori(nc, azienda=azienda)['totale']
    return {
        'fatture': fatture[:limite],
        'n_fatture': len(fatture),
        'note_credito': note,
        'offerte': list(TestataOfferta.objects.filter(cliente=cliente).order_by('-anno', '-numero')[:10]),
        'fatturato': fatturato,
        'incassato': incassato,
        'da_incassare': da_incassare,
        'scaduto': scaduto,
        'oggi': oggi,
    }
