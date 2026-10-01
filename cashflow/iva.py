"""Liquidazione IVA stimata dalle fatture del gestionale.

Per ogni periodo (mese o trimestre, ``AnagraficaAzienda.liquidazione_iva``):

- IVA a debito: le fatture emesse nel periodo (data documento), meno le note
  di credito emesse. Split payment (esigibilità «S»): l'IVA la versa il
  cliente, qui solo come informazione. Esigibilità differita («D»): l'IVA
  diventa esigibile quando la fattura è pagata, quindi conta nel periodo del
  pagamento; finché non è pagata è «in sospeso». Le fatture scartate dal SDI
  non contano.
- IVA a credito: le fatture di acquisto confermate, nel periodo in cui sono
  registrate, meno le note di credito dei fornitori (TD04). Le integrazioni
  del reverse charge (TD16-TD19) contano due volte, a debito e a credito.
- Saldo: debito − credito, più il credito o il debito minimo riportato dal
  periodo prima. Trimestrale: + 1% di interessi su quanto si versa. Sotto
  25,82 € non si versa e si riporta.
- Scadenza: il 16 del mese dopo (mensile); 16 maggio, 20 agosto, 16 novembre
  e 16 marzo dell'anno dopo per il quarto trimestre (trimestrale). Sabato e
  domenica slittano al lunedì.

È una STIMA: niente acconto di dicembre, festività, IVA per cassa, plafond,
pro-rata o rettifiche. ``sincronizza`` la tiene allineata nelle scadenze
fiscali (``stimata=True``) finché non la si modifica a mano o la si paga.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils.timezone import localdate

ZERO = Decimal('0.00')
MINIMO_VERSAMENTO = Decimal('25.82')
INTERESSI_TRIMESTRALI = Decimal('0.01')
# regimi senza IVA da liquidare (forfettario, minimi) o per cassa (stima non valida)
REGIMI_SENZA_IVA = {'RF02', 'RF19'}
REGIMI_PER_CASSA = {'RF16', 'RF17'}
MESI = ('gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 'luglio',
        'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre')


@dataclass
class Periodo:
    chiave: str              # «2026-T3» o «2026-09»
    etichetta: str
    inizio: date
    fine: date
    scadenza: date
    trimestrale: bool
    # importi
    vendite: Decimal = ZERO
    note_credito: Decimal = ZERO
    integrazioni: Decimal = ZERO        # reverse charge: sia debito sia credito
    acquisti: Decimal = ZERO
    note_credito_fornitori: Decimal = ZERO
    split_payment: Decimal = ZERO       # solo informativo
    in_sospeso: Decimal = ZERO          # differita non ancora pagata
    credito_precedente: Decimal = ZERO
    debito_precedente: Decimal = ZERO
    interessi: Decimal = ZERO
    da_versare: Decimal = ZERO
    credito_riportato: Decimal = ZERO
    debito_riportato: Decimal = ZERO
    documenti: int = 0
    in_corso: bool = False

    @property
    def debito(self) -> Decimal:
        return self.vendite - self.note_credito + self.integrazioni

    @property
    def credito(self) -> Decimal:
        return self.acquisti - self.note_credito_fornitori + self.integrazioni

    @property
    def saldo(self) -> Decimal:
        return self.debito - self.credito


@dataclass
class Liquidazione:
    anno: int
    periodicita: str
    periodi: list[Periodo] = field(default_factory=list)
    avvisi: list[str] = field(default_factory=list)


def _lunedi_se_festivo(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def periodi(anno: int, periodicita: str) -> list[Periodo]:
    out = []
    if periodicita == 'mensile':
        for m in range(1, 13):
            inizio = date(anno, m, 1)
            fine = (date(anno + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
            scad = date(anno + (m == 12), m % 12 + 1, 16)
            out.append(Periodo(f'{anno}-{m:02d}', f'{MESI[m - 1]} {anno}', inizio, fine,
                               _lunedi_se_festivo(scad), False))
    else:
        scadenze = {1: date(anno, 5, 16), 2: date(anno, 8, 20), 3: date(anno, 11, 16),
                    4: date(anno + 1, 3, 16)}
        for t in range(1, 5):
            inizio = date(anno, 3 * t - 2, 1)
            fine = date(anno + (t == 4), (3 * t) % 12 + 1, 1) - timedelta(days=1)
            out.append(Periodo(f'{anno}-T{t}', f'{t}° trimestre {anno}', inizio, fine,
                               _lunedi_se_festivo(scadenze[t]), True))
    return out


def _azienda():
    from anagrafiche.models import AnagraficaAzienda
    return AnagraficaAzienda.objects.select_related('profilo_fiscale').first()


def periodicita_attiva() -> str:
    """'mensile', 'trimestrale' o '' (nessuna stima: non configurata o regime
    senza IVA da liquidare)."""
    az = _azienda()
    if az is None or not az.liquidazione_iva:
        return ''
    regime = az.profilo_fiscale.regime_fiscale if az.profilo_fiscale_id else 'RF01'
    return '' if regime in REGIMI_SENZA_IVA else az.liquidazione_iva


def _esigibilita(fattura) -> str:
    """«I», «D» o «S» per una fattura emessa: lo split payment o la differita
    delle posizioni IVA delle righe, altrimenti il profilo fiscale del cliente."""
    tipi = set()
    for r in fattura.righe.all():
        if r.iva is not None:
            if r.iva.scissione_pagamenti or r.iva.esigibilita_iva == 'S':
                tipi.add('S')
            elif r.iva.esigibilita_iva == 'D':
                tipi.add('D')
    if 'S' in tipi:
        return 'S'
    if 'D' in tipi:
        return 'D'
    pf = getattr(fattura.cliente, 'profilo_fiscale', None) if fattura.cliente_id else None
    if pf is not None and pf.esigibilita_iva_id:
        return pf.esigibilita_iva.tipo
    return 'I'


def _imposta(doc) -> Decimal:
    from documenti.utils import calcola_valori
    return calcola_valori(doc, list(doc.righe.all()))['imposta_totale']


def _in(p: Periodo, d: date | None) -> bool:
    return d is not None and p.inizio <= d <= p.fine


def _riempi(per: list[Periodo]) -> None:
    """Somma le imposte dei documenti nei periodi."""
    from documenti.models import TestataFattura, TestataFatturaAcquisto, TestataNotaCredito

    inizio, fine = per[0].inizio, per[-1].fine
    righe = 'righe__iva'
    fatture = (TestataFattura.objects
               .exclude(sdi_stato=TestataFattura.SdiStato.SCARTATA)
               .filter(data_documento__lte=fine)
               .filter(data_documento__gte=inizio - timedelta(days=3 * 366))
               .select_related('cliente__profilo_fiscale__esigibilita_iva')
               .prefetch_related(righe))
    for f in fatture:
        esig = _esigibilita(f)
        quando = f.data_pagamento if esig == 'D' else f.data_documento
        if esig != 'D' and not (inizio <= f.data_documento <= fine):
            continue
        imposta = _imposta(f)
        for p in per:
            if esig == 'S' and _in(p, f.data_documento):
                p.split_payment += imposta
                p.documenti += 1
            elif esig == 'D' and not f.pagata and _in(p, f.data_documento):
                p.in_sospeso += imposta
                p.documenti += 1
            elif esig != 'S' and (f.pagata or esig != 'D') and _in(p, quando):
                p.vendite += imposta
                p.documenti += 1
    for nc in (TestataNotaCredito.objects.filter(data_documento__range=(inizio, fine))
               .prefetch_related(righe)):
        for p in per:
            if _in(p, nc.data_documento):
                p.note_credito += _imposta(nc)
                p.documenti += 1
    acquisti = TestataFatturaAcquisto.objects.filter(
        stato=TestataFatturaAcquisto.StatoFattura.CONFERMATA)
    integrazioni = {'TD16', 'TD17', 'TD18', 'TD19'}
    for a in acquisti:
        quando = a.data_registrazione or a.data_documento
        imposta = a.imposta or ZERO
        for p in per:
            if not _in(p, quando):
                continue
            p.documenti += 1
            if a.tipo_documento in integrazioni:
                p.integrazioni += imposta
            elif a.tipo_documento == 'TD04':
                p.note_credito_fornitori += imposta
            else:
                p.acquisti += imposta


def _riporta(per: list[Periodo], credito: Decimal, debito: Decimal) -> tuple[Decimal, Decimal]:
    """Saldo, interessi e importo da versare di ogni periodo, con i riporti.
    Restituisce credito e debito minimo riportati dopo l'ultimo periodo."""
    for p in per:
        p.credito_precedente, p.debito_precedente = credito, debito
        netto = p.saldo - credito + debito
        credito = debito = ZERO
        if netto <= 0:
            credito = -netto
            p.credito_riportato = credito
            continue
        interessi = (netto * INTERESSI_TRIMESTRALI).quantize(ZERO) if p.trimestrale else ZERO
        if netto + interessi < MINIMO_VERSAMENTO:
            debito = netto
            p.debito_riportato = netto
            continue
        p.interessi = interessi
        p.da_versare = (netto + interessi).quantize(ZERO)
    return credito, debito


def _primo_anno() -> int | None:
    from documenti.models import TestataFattura, TestataFatturaAcquisto
    date_ = [d for d in (
        TestataFattura.objects.order_by('data_documento').values_list('data_documento', flat=True).first(),
        TestataFatturaAcquisto.objects.order_by('data_documento')
        .values_list('data_documento', flat=True).first()) if d]
    return min(date_).year if date_ else None


def calcola(anno: int, periodicita: str | None = None) -> Liquidazione:
    """La liquidazione stimata dell'anno, con i riporti dagli anni prima
    (dal primo anno con documenti)."""
    periodicita = periodicita_attiva() if periodicita is None else periodicita
    liq = Liquidazione(anno=anno, periodicita=periodicita)
    if not periodicita:
        return liq
    oggi = localdate()
    credito = debito = ZERO
    primo = _primo_anno() or anno
    for a in range(min(primo, anno), anno + 1):
        per = periodi(a, periodicita)
        _riempi(per)
        credito, debito = _riporta(per, credito, debito)
        if a == anno:
            for p in per:
                p.in_corso = p.inizio <= oggi <= p.fine
            liq.periodi = per
    az = _azienda()
    regime = az.profilo_fiscale.regime_fiscale if az and az.profilo_fiscale_id else 'RF01'
    if regime in REGIMI_PER_CASSA:
        liq.avvisi.append("Il regime fiscale è IVA per cassa: l'IVA diventa esigibile all'incasso, "
                          "questa stima per data della fattura non è affidabile.")
    if any(p.in_sospeso for p in liq.periodi):
        liq.avvisi.append("Ci sono fatture a esigibilità differita non ancora pagate: la loro IVA "
                          "entra nel periodo in cui le segni pagate.")
    liq.avvisi.append("Non calcolati: acconto IVA di dicembre (27/12), festività, plafond, "
                      "pro-rata e rettifiche. Fai verificare al commercialista prima di versare.")
    return liq


@transaction.atomic
def sincronizza(oggi: date | None = None) -> int:
    """Allinea le scadenze fiscali stimate ai periodi già iniziati e non ancora
    scaduti (anche il quarto trimestre dell'anno prima, che si versa a marzo).
    Non tocca le scadenze pagate o modificate a mano. Restituisce quante ne ha
    create, aggiornate o tolte."""
    from .models import ScadenzaFiscale, TipoScadenzaFiscale

    periodicita = periodicita_attiva()
    if not periodicita:
        return 0
    oggi = oggi or localdate()
    toccate = 0
    for anno in (oggi.year - 1, oggi.year):
        for p in calcola(anno, periodicita).periodi:
            # solo i versamenti ancora da fare: un periodo già scaduto si
            # vede nella pagina, ma non diventa un debito «scaduto» nelle
            # scadenze (magari è già stato versato fuori dal gestionale)
            if p.inizio > oggi or p.scadenza < oggi:
                continue
            esistente = ScadenzaFiscale.objects.filter(periodo_iva=p.chiave).first()
            if esistente is not None and (esistente.pagata or not esistente.stimata):
                continue
            if p.da_versare <= 0:
                if esistente is not None:
                    esistente.delete()
                    toccate += 1
                continue
            dati = {
                'data_scadenza': p.scadenza, 'tipo': TipoScadenzaFiscale.IVA,
                'importo': p.da_versare, 'stimata': True,
                'descrizione': f'Liquidazione IVA {p.etichetta} (stima)'
                               + (' — periodo in corso' if p.in_corso else ''),
            }
            if esistente is None:
                ScadenzaFiscale.objects.create(periodo_iva=p.chiave, **dati)
                toccate += 1
            elif any(getattr(esistente, k) != v for k, v in dati.items()):
                for k, v in dati.items():
                    setattr(esistente, k, v)
                esistente.save()
                toccate += 1
    return toccate
