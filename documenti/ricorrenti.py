"""Fatture ricorrenti: quali periodi emettere e come.

Una ``FatturaRicorrente`` prevede un'emissione per periodo (mensile,
trimestrale…) nel ``giorno_emissione`` del mese (31 = l'ultimo giorno, come
le spese ricorrenti). Ogni periodo emesso, o tentato, è una
``EmissioneRicorrente``: il vincolo (ricorrenza, periodo) impedisce di
fatturarlo due volte, anche con il timer e il pulsante «Emetti ora» insieme.

La fattura si crea con ``servizi.crea_documento('fattura', …)``, come da API
e chat: stessa numerazione, righe e scadenze dalla forma di pagamento. La
sua data è sempre il giorno dell'emissione, mai nel passato: recuperando
periodi saltati le fatture sono datate oggi e il periodo di competenza sta
nella descrizione, così numeri e date restano progressivi nell'anno.

Nelle descrizioni e nelle note si possono scrivere ``{mese}``, ``{anno}``,
``{periodo}`` («ottobre 2026», «1° trimestre 2026»…), ``{dal}`` e ``{al}``
(le date del periodo di competenza).
"""
from __future__ import annotations

import logging
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

from django.db import transaction
from django.utils.timezone import localdate

from .models import EmissioneRicorrente, FatturaRicorrente, RigaDocumento, TestataFattura

logger = logging.getLogger(__name__)

MESI = ('gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 'luglio', 'agosto',
        'settembre', 'ottobre', 'novembre', 'dicembre')
_NOME_BLOCCO = {3: 'trimestre', 4: 'quadrimestre', 6: 'semestre'}
SEGNAPOSTO = ('mese', 'anno', 'periodo', 'dal', 'al')
_RE_SEGNAPOSTO = re.compile(r'\{(' + '|'.join(SEGNAPOSTO) + r')\}')


@dataclass(frozen=True)
class Periodo:
    previsto: date      # data prevista di emissione: la chiave dell'emissione
    dal: date           # periodo di competenza
    al: date
    etichetta: str      # «ottobre 2026», «1° trimestre 2026»…


def mesi_per_periodo(ricorrenza) -> int:
    from cashflow.models import _MESI_PER_PERIODO
    return _MESI_PER_PERIODO[ricorrenza.periodicita]


def _giorno_in_mese(anno: int, mese: int, giorno: int) -> date:
    return date(anno, mese, max(1, min(giorno, monthrange(anno, mese)[1])))


def _sposta(anno: int, mese: int, mesi: int) -> tuple[int, int]:
    indice = anno * 12 + (mese - 1) + mesi
    return indice // 12, indice % 12 + 1


def date_previste(ricorrenza, fino_a: date) -> list[date]:
    """Le date di emissione da ``data_inizio`` a ``fino_a`` (e non oltre
    ``data_fine``), un periodo ogni N mesi a partire dal mese d'inizio."""
    fine = min(fino_a, ricorrenza.data_fine) if ricorrenza.data_fine else fino_a
    if fine < ricorrenza.data_inizio:
        return []
    passo = mesi_per_periodo(ricorrenza)
    anno, mese = ricorrenza.data_inizio.year, ricorrenza.data_inizio.month
    out = []
    while True:
        d = _giorno_in_mese(anno, mese, ricorrenza.giorno_emissione)
        if d > fine:
            return out
        if d >= ricorrenza.data_inizio:
            out.append(d)
        anno, mese = _sposta(anno, mese, passo)


def periodo(ricorrenza, previsto: date) -> Periodo:
    """Il periodo di competenza dell'emissione prevista il giorno
    ``previsto``: il blocco di calendario (mese, trimestre, semestre…) che lo
    contiene, o quello prima se la ricorrenza fattura a consuntivo."""
    passo = mesi_per_periodo(ricorrenza)
    anno, mese = previsto.year, ((previsto.month - 1) // passo) * passo + 1
    if ricorrenza.competenza == FatturaRicorrente.Competenza.PRECEDENTE:
        anno, mese = _sposta(anno, mese, -passo)
    fine_anno, fine_mese = _sposta(anno, mese, passo - 1)
    dal = date(anno, mese, 1)
    al = date(fine_anno, fine_mese, monthrange(fine_anno, fine_mese)[1])
    if passo == 1:
        etichetta = f'{MESI[mese - 1]} {anno}'
    elif passo == 2:
        etichetta = f'{MESI[mese - 1]}-{MESI[fine_mese - 1]} {anno}'
    elif passo == 12:
        etichetta = str(anno)
    else:
        etichetta = f'{(mese - 1) // passo + 1}° {_NOME_BLOCCO[passo]} {anno}'
    return Periodo(previsto, dal, al, etichetta)


def segnaposto(testo: str, p: Periodo) -> str:
    valori = {'mese': MESI[p.dal.month - 1], 'anno': str(p.dal.year), 'periodo': p.etichetta,
              'dal': f'{p.dal:%d/%m/%Y}', 'al': f'{p.al:%d/%m/%Y}'}
    return _RE_SEGNAPOSTO.sub(lambda m: valori[m.group(1)], testo or '')


_CHIUSI = (EmissioneRicorrente.Esito.EMESSA, EmissioneRicorrente.Esito.SALTATA)


def _emessi(ricorrenza) -> set[date]:
    # .all(): usa le emissioni già caricate (prefetch_related della lista)
    return {e.periodo for e in ricorrenza.emissioni.all() if e.esito in _CHIUSI}


def da_emettere(ricorrenza, oggi: date | None = None) -> list[Periodo]:
    """I periodi previsti fino a oggi e non ancora emessi (quelli non
    riusciti si riprovano). Nessuno se la ricorrenza è sospesa."""
    if not ricorrenza.attiva:
        return []
    oggi = oggi or localdate()
    emessi = _emessi(ricorrenza)
    return [periodo(ricorrenza, d) for d in date_previste(ricorrenza, oggi) if d not in emessi]


def prossime(ricorrenza, oggi: date | None = None, quante: int = 3) -> list[Periodo]:
    """Le prossime emissioni dopo oggi (per l'interfaccia e il «Da fare»)."""
    oggi = oggi or localdate()
    if not ricorrenza.attiva:
        return []
    out = []
    da = max(oggi, ricorrenza.data_inizio)
    fino_a = da + timedelta(days=31 * mesi_per_periodo(ricorrenza) * quante + 31)
    for d in date_previste(ricorrenza, fino_a):
        if d > oggi:
            out.append(periodo(ricorrenza, d))
            if len(out) == quante:
                break
    return out


def stato(ricorrenza, oggi: date | None = None) -> str:
    oggi = oggi or localdate()
    if ricorrenza.data_fine and ricorrenza.data_fine < oggi and not da_emettere(ricorrenza, oggi):
        return 'terminata'
    return 'attiva' if ricorrenza.attiva else 'sospesa'


# ---------------------------------------------------------------------------
# Totali (senza salvare)
# ---------------------------------------------------------------------------


def righe_documento(ricorrenza, righe=None, p: Periodo | None = None) -> list[RigaDocumento]:
    """Le righe del modello come ``RigaDocumento`` non salvate (IVA del
    cliente dove manca): per i totali con ``calcola_valori``."""
    righe = list(ricorrenza.righe.all()) if righe is None else righe
    iva_cliente = ricorrenza.cliente.posizione_iva if ricorrenza.cliente_id else None
    return [RigaDocumento(
        numero_riga=i, articolo=r.articolo, quantita=r.quantita, um=r.um,
        importo_unitario=r.importo_unitario, iva=r.iva or iva_cliente,
        descrizione_libera=segnaposto(r.descrizione_libera or '', p) if p else r.descrizione_libera,
    ) for i, r in enumerate(righe, start=1)]


def valori_periodo(ricorrenza, righe=None, azienda=None) -> dict:
    """Imponibile, IVA e totale di una fattura del periodo (per la lista e
    il riepilogo del form), con gli stessi conti di PDF e FatturaPA."""
    from .utils import _DA_CARICARE, calcola_valori
    testata = TestataFattura(cliente=ricorrenza.cliente if ricorrenza.cliente_id else None,
                             tipo_documento=ricorrenza.tipo_documento, data_documento=localdate())
    return calcola_valori(testata, righe_documento(ricorrenza, righe),
                          azienda=azienda if azienda is not None else _DA_CARICARE)


# ---------------------------------------------------------------------------
# Emissione
# ---------------------------------------------------------------------------


def dati_fattura(ricorrenza, p: Periodo, oggi: date) -> dict:
    """I dati per ``servizi.crea_documento``: la fattura di oggi per il
    periodo ``p``."""
    righe = []
    for r in ricorrenza.righe.select_related('articolo'):
        riga = {'descrizione': segnaposto(r.descrizione_libera or '', p), 'quantita': str(r.quantita),
                'importo_unitario': str(r.importo_unitario), 'um': r.um or None}
        if r.iva_id:
            riga['iva_id'] = r.iva_id
        if r.articolo is not None:
            riga['articolo'] = r.articolo.codice
        righe.append(riga)
    dati = {
        'cliente_id': ricorrenza.cliente_id, 'data_documento': oggi.isoformat(),
        'tipo_documento': ricorrenza.tipo_documento, 'sezionale': ricorrenza.sezionale,
        'note': segnaposto(ricorrenza.note, p), 'righe': righe,
    }
    if ricorrenza.forme_pagamento_id:
        dati['forme_pagamento_id'] = ricorrenza.forme_pagamento_id
    if ricorrenza.conto_corrente_id:
        dati['conto_corrente_id'] = ricorrenza.conto_corrente_id
    return dati


def emetti(ricorrenza, p: Periodo, oggi: date | None = None, *, invia: bool = True):
    """Emette la fattura del periodo ``p`` e la registra. Un errore (dati
    non validi, cliente senza IVA…) resta registrato sull'emissione e non
    solleva: chi emette più ricorrenze prosegue con le altre. Ritorna
    l'``EmissioneRicorrente`` (None se il periodo era già stato emesso)."""
    from .servizi import DatiNonValidi, crea_documento

    oggi = oggi or localdate()
    try:
        with transaction.atomic():
            # ricontrollato dentro la transazione (che su SQLite è IMMEDIATE:
            # un solo scrittore alla volta), contro due emissioni concorrenti
            if ricorrenza.emissioni.filter(periodo=p.previsto, esito__in=_CHIUSI).exists():
                return None
            fattura = crea_documento('fattura', dati_fattura(ricorrenza, p, oggi))
            emissione, _ = EmissioneRicorrente.objects.update_or_create(
                ricorrenza=ricorrenza, periodo=p.previsto,
                defaults={'fattura': fattura, 'esito': EmissioneRicorrente.Esito.EMESSA,
                          'errore': '', 'invio': ''},
            )
    except DatiNonValidi as exc:
        return _errore(ricorrenza, p, '; '.join(f'{_campo(k)}: {v}' for k, v in exc.errori.items()))
    except Exception as exc:  # non deve fermare le altre ricorrenze
        logger.exception('Fattura ricorrente %s, periodo %s: emissione non riuscita', ricorrenza.pk,
                         p.previsto)
        return _errore(ricorrenza, p, f'{exc.__class__.__name__}: {exc}')
    if invia:
        _invio_automatico(ricorrenza, emissione)
    return emissione


def _campo(chiave: str) -> str:
    # 'righe.0.iva_id' → 'riga 1'
    m = re.match(r'righe\.(\d+)', chiave)
    return f'riga {int(m.group(1)) + 1}' if m else chiave


def _errore(ricorrenza, p: Periodo, testo: str):
    emissione, _ = EmissioneRicorrente.objects.update_or_create(
        ricorrenza=ricorrenza, periodo=p.previsto,
        defaults={'fattura': None, 'esito': EmissioneRicorrente.Esito.ERRORE, 'errore': testo[:2000]},
    )
    return emissione


def _invio_automatico(ricorrenza, emissione) -> None:
    """SDI ed email dopo l'emissione, se la ricorrenza li prevede. Un invio
    non riuscito non annulla la fattura: resta scritto sull'emissione e la
    fattura si invia a mano dal suo dettaglio."""
    from .invio import invia_al_sdi, invia_email, sdi_attivo

    fattura = emissione.fattura
    esiti = []
    if ricorrenza.invia_sdi:
        if sdi_attivo():
            esiti.append(f'SDI: {invia_al_sdi(fattura).messaggio}')
        else:
            esiti.append('SDI: non inviata, l\'invio al SDI è disabilitato nelle impostazioni.')
    if ricorrenza.invia_email:
        fattura.refresh_from_db()
        esiti.append(f'Email: {invia_email(fattura).messaggio}')
    if esiti:
        EmissioneRicorrente.objects.filter(pk=emissione.pk).update(invio='\n'.join(esiti)[:2000])
        emissione.invio = '\n'.join(esiti)


def emetti_scadute(ricorrenza, oggi: date | None = None) -> list:
    """Tutti i periodi da emettere della ricorrenza, in ordine: numeri di
    fattura in sequenza."""
    oggi = oggi or localdate()
    return [e for e in (emetti(ricorrenza, p, oggi) for p in da_emettere(ricorrenza, oggi)) if e]


def genera(oggi: date | None = None) -> list:
    """Le emissioni di tutte le ricorrenze attive (il comando del timer)."""
    oggi = oggi or localdate()
    out = []
    for r in FatturaRicorrente.objects.filter(attiva=True).select_related('cliente').order_by('pk'):
        out.extend(emetti_scadute(r, oggi))
    return out


def salta_periodi(ricorrenza, oggi: date | None = None) -> int:
    """Alla riattivazione: i periodi passati mentre era sospesa non si
    fatturano (restano nello storico come saltati)."""
    oggi = oggi or localdate()
    saltati = 0
    for p in da_emettere(ricorrenza, oggi):
        EmissioneRicorrente.objects.update_or_create(
            ricorrenza=ricorrenza, periodo=p.previsto,
            defaults={'fattura': None, 'esito': EmissioneRicorrente.Esito.SALTATA,
                      'errore': 'Periodo passato mentre la ricorrenza era sospesa.'},
        )
        saltati += 1
    return saltati


# ---------------------------------------------------------------------------
# Cashflow
# ---------------------------------------------------------------------------


def incassi_previsti(oggi: date, fino_a: date) -> list[dict]:
    """Gli incassi delle fatture ricorrenti non ancora emesse, per la
    timeline del cashflow: una fattura per periodo (i periodi arretrati come
    se uscissero oggi), divisa nelle scadenze della forma di pagamento."""
    from django.urls import reverse

    from .utils import azienda_per_calcoli, genera_scadenze_da_forma_pagamento

    azienda = azienda_per_calcoli()
    qs = (FatturaRicorrente.objects.filter(attiva=True)
          .select_related('cliente__posizione_iva', 'forme_pagamento', 'cliente__forme_pagamento')
          .prefetch_related('righe__iva', 'righe__articolo', 'emissioni',
                            'forme_pagamento__scadenze', 'cliente__forme_pagamento__scadenze'))
    eventi = []
    for r in qs:
        emessi = _emessi(r)
        totale = valori_periodo(r, azienda=azienda)['totale']
        forma = r.forme_pagamento or r.cliente.forme_pagamento
        url = reverse('documenti:ricorrente_detail', args=[r.pk])
        for d in date_previste(r, fino_a):
            if d in emessi:
                continue
            p = periodo(r, d)
            emissione = max(d, oggi)
            testata = TestataFattura(data_documento=emissione, forme_pagamento=forma)
            rate = (genera_scadenze_da_forma_pagamento(testata, totale=totale) if forma else []) \
                or [{'data': emissione, 'importo': totale}]
            for rata in rate:
                if rata['data'] <= fino_a:
                    eventi.append({
                        'data': rata['data'], 'tipo': 'incasso', 'origine': 'Fattura ricorrente (prevista)',
                        'descrizione': f'{r.nome} — {p.etichetta}', 'importo': rata['importo'],
                        'scaduta': False, 'url': url,
                    })
    return eventi
