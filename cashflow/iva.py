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


def da_chiave(chiave: str) -> Periodo | None:
    """Il periodo di una chiave («2026-T3», «2026-09»), None se non lo è."""
    try:
        anno, parte = chiave.split('-')
        anno = int(anno)
        if parte.startswith('T'):
            return periodi(anno, 'trimestrale')[int(parte[1:]) - 1]
        return periodi(anno, 'mensile')[int(parte) - 1]
    except (ValueError, IndexError, AttributeError):
        return None


def etichetta_periodo(chiave: str) -> str:
    """«3° trimestre 2026 · 01/07 – 30/09/2026», o '' se la chiave non è un periodo."""
    p = da_chiave(chiave)
    if p is None:
        return ''
    return f'{p.etichetta} · {p.inizio:%d/%m} – {p.fine:%d/%m/%Y}'


def scelte_periodo(periodicita: str, oggi: date | None = None) -> list[tuple[str, str]]:
    """I periodi da proporre nel modulo di una scadenza IVA: anno scorso, anno
    in corso e prossimo, del tipo configurato (trimestrale se non c'è)."""
    oggi = oggi or localdate()
    tipo = periodicita or 'trimestrale'
    return [(p.chiave, f'{p.etichetta} ({p.inizio:%d/%m} – {p.fine:%d/%m/%Y})'.capitalize())
            for anno in (oggi.year + 1, oggi.year, oggi.year - 1)
            for p in reversed(periodi(anno, tipo))]


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


def _imposta(doc, azienda) -> Decimal:
    from documenti.utils import calcola_valori
    return calcola_valori(doc, list(doc.righe.all()), azienda=azienda)['imposta_totale']


def _in(p: Periodo, d: date | None) -> bool:
    return d is not None and p.inizio <= d <= p.fine


def _riempi(per: list[Periodo], azienda=None) -> None:
    """Somma le imposte dei documenti nei periodi."""
    from django.db.models import Q

    from documenti.models import TestataFattura, TestataFatturaAcquisto, TestataNotaCredito

    inizio, fine = per[0].inizio, per[-1].fine
    righe = 'righe__iva'
    # contano le fatture emesse nell'anno e, per l'esigibilità differita,
    # quelle pagate nell'anno (anche se emesse prima)
    fatture = (TestataFattura.objects
               .exclude(sdi_stato=TestataFattura.SdiStato.SCARTATA)
               .filter(Q(data_documento__range=(inizio, fine))
                       | Q(data_documento__lt=inizio, data_pagamento__range=(inizio, fine)))
               .select_related('cliente__profilo_fiscale__esigibilita_iva')
               .prefetch_related(righe))
    for f in fatture:
        esig = _esigibilita(f)
        quando = f.data_pagamento if esig == 'D' else f.data_documento
        if esig != 'D' and not (inizio <= f.data_documento <= fine):
            continue
        imposta = _imposta(f, azienda)
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
                p.note_credito += _imposta(nc, azienda)
                p.documenti += 1
    acquisti = TestataFatturaAcquisto.objects.filter(
        Q(data_registrazione__range=(inizio, fine))
        | Q(data_registrazione__isnull=True, data_documento__range=(inizio, fine)),
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


def _calcola_anni(da: int, fino_a: int, periodicita: str) -> dict[int, list[Periodo]]:
    """I periodi di ogni anno da ``da`` (o dal primo anno con documenti, se
    viene prima) a ``fino_a``, con i riporti: un solo passaggio sugli anni."""
    from documenti.utils import azienda_per_calcoli

    azienda = azienda_per_calcoli()
    oggi = localdate()
    credito = debito = ZERO
    primo = _primo_anno() or da
    anni = {}
    for a in range(min(primo, da), fino_a + 1):
        per = periodi(a, periodicita)
        _riempi(per, azienda)
        credito, debito = _riporta(per, credito, debito)
        for p in per:
            p.in_corso = p.inizio <= oggi <= p.fine
        anni[a] = per
    return anni


def calcola(anno: int, periodicita: str | None = None) -> Liquidazione:
    """La liquidazione stimata dell'anno, con i riporti dagli anni prima
    (dal primo anno con documenti)."""
    periodicita = periodicita_attiva() if periodicita is None else periodicita
    liq = Liquidazione(anno=anno, periodicita=periodicita)
    if not periodicita:
        return liq
    liq.periodi = _calcola_anni(anno, anno, periodicita)[anno]
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


def sincronizza(oggi: date | None = None) -> int:
    """Allinea le scadenze fiscali stimate ai periodi già iniziati che scadono
    da inizio anno in poi (anche il quarto trimestre dell'anno prima, che si
    versa a marzo), salvo quelli già inseriti a mano.
    Non tocca le scadenze pagate o modificate a mano. Restituisce quante ne ha
    create, aggiornate o tolte.

    Gira a ogni apertura delle pagine del cashflow: il calcolo e il confronto
    sono solo letture, la transazione (e il lock sul database) si apre solo
    se c'è qualcosa da scrivere."""
    periodicita = periodicita_attiva()
    if not periodicita:
        return 0
    oggi = oggi or localdate()
    inizio_anno = date(oggi.year, 1, 1)
    anni = _calcola_anni(oggi.year - 1, oggi.year, periodicita)
    # i periodi già iniziati con scadenza da inizio anno in poi, anche quelle
    # già passate (si segnano pagate a mano); quelle degli anni prima no, per
    # non riempire le scadenze di vecchi debiti
    candidati = [p for anno in (oggi.year - 1, oggi.year) for p in anni.get(anno, [])
                 if not (p.inizio > oggi or p.scadenza < inizio_anno)]
    if not _operazioni(candidati):
        return 0
    with transaction.atomic():
        # di nuovo sotto il lock: un'altra richiesta può averle già fatte
        operazioni = _operazioni(candidati)
        for op in operazioni:
            op()
    return len(operazioni)


def _operazioni(candidati: list[Periodo]) -> list:
    """Le scritture che servono per allineare le scadenze stimate ai periodi
    (funzioni da chiamare), senza farle."""
    from .models import ScadenzaFiscale, TipoScadenzaFiscale

    esistenti = {s.periodo_iva: s for s in ScadenzaFiscale.objects.filter(
        periodo_iva__in=[p.chiave for p in candidati])}
    operazioni = []
    for p in candidati:
        esistente = esistenti.get(p.chiave)
        if esistente is not None and (esistente.pagata or not esistente.stimata):
            continue
        # già inserita a mano, senza periodo: una scadenza IVA tra la fine
        # del periodo e qualche giorno dopo la scadenza. Le si dà il
        # periodo di riferimento e non si crea la stima
        if esistente is None:
            manuale = ScadenzaFiscale.objects.filter(
                periodo_iva='', tipo=TipoScadenzaFiscale.IVA,
                data_scadenza__range=(p.fine + timedelta(days=1),
                                      p.scadenza + timedelta(days=15))).first()
            if manuale is not None:
                operazioni.append(lambda pk=manuale.pk, chiave=p.chiave: (
                    ScadenzaFiscale.objects.filter(pk=pk).update(periodo_iva=chiave)))
                continue
        if p.da_versare <= 0:
            if esistente is not None:
                operazioni.append(esistente.delete)
            continue
        dati = {
            'data_scadenza': p.scadenza, 'tipo': TipoScadenzaFiscale.IVA,
            'importo': p.da_versare, 'stimata': True,
            # «stima» e «periodo in corso» non stanno nel testo, che resta
            # anche dopo la modifica a mano o il pagamento: li mostra
            # l'etichetta, dallo stato (ScadenzaFiscale.periodo_iva_in_corso)
            'descrizione': f'Liquidazione IVA {p.etichetta}',
        }
        if esistente is None:
            operazioni.append(lambda chiave=p.chiave, dati=dati: (
                ScadenzaFiscale.objects.create(periodo_iva=chiave, **dati)))
        elif any(getattr(esistente, k) != v for k, v in dati.items()):
            def aggiorna(esistente=esistente, dati=dati):
                for k, v in dati.items():
                    setattr(esistente, k, v)
                esistente.save()
            operazioni.append(aggiorna)
    return operazioni


@transaction.atomic
def cambia_periodicita(nuova: str) -> int:
    """Imposta la periodicità della liquidazione IVA ('mensile', 'trimestrale'
    o '' per non calcolarla) e rifà le stime: quelle dell'altro schema (o
    tutte, se spenta) ancora da pagare si tolgono. Le scadenze pagate o
    modificate a mano restano. Restituisce quante stime ha toccato."""
    from .models import ScadenzaFiscale

    az = _azienda()
    if az is None:
        raise ValueError("Compila prima l'anagrafica dell'azienda.")
    az.liquidazione_iva = nuova
    az.save(update_fields=['liquidazione_iva'])
    da_togliere = ScadenzaFiscale.objects.filter(stimata=True, pagata=False).exclude(periodo_iva='')
    if nuova == 'mensile':
        da_togliere = da_togliere.filter(periodo_iva__contains='-T')
    elif nuova == 'trimestrale':
        da_togliere = da_togliere.exclude(periodo_iva__contains='-T')
    tolte = da_togliere.delete()[0]
    return tolte + sincronizza()
