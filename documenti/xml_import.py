"""
Import XML FatturaPA (ciclo passivo).

Accetta file XML in chiaro (non supporta .p7m firmati). Estrae cedente,
testata, righe, scadenze e crea una ``TestataFatturaAcquisto`` in stato
``bozza`` pronta per essere rivista dall'utente.

Deduplica per (fornitore, numero_fornitore, data_fornitore).
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.files.base import ContentFile
from django.db import transaction
from lxml import etree

from anagrafiche.models import Fornitore, PosizioneIva
from .models import (
    RigaFatturaAcquisto,
    ScadenzaFatturaAcquisto,
    TestataFatturaAcquisto,
)


_MODALITA_PAGAMENTO_VALID = {f'MP{i:02d}' for i in range(1, 18)}

_UM_MAP = {
    'PZ': 'PZ', 'MQ': 'MQ', 'ML': 'ML', 'KG': 'KG',
    'LT': 'LT', 'H': 'H', 'N': 'N',
}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.split('}', 1)[1] if '}' in tag else tag


def _find(element, path):
    current = element
    for name in path:
        if current is None:
            return None
        found = None
        for child in current:
            if _local(child.tag) == name:
                found = child
                break
        current = found
    return current


def _findall(element, name):
    if element is None:
        return []
    return [c for c in element if _local(c.tag) == name]


def _text(element, path, default=''):
    node = _find(element, path)
    if node is None or node.text is None:
        return default
    return node.text.strip()


def _to_decimal(value):
    if not value:
        return Decimal('0')
    try:
        return Decimal(value.replace(',', '.'))
    except (InvalidOperation, AttributeError):
        return Decimal('0')


def _to_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


def parse_fattura_xml(xml_bytes: bytes) -> dict[str, Any]:
    """Estrae i dati da un FatturaPA v1.2 (FPR12/FPA12).

    Solleva ``ValueError`` con messaggio leggibile se il file non è valido.
    """
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as exc:
        raise ValueError(f'XML non valido: {exc}') from exc

    if _local(root.tag) != 'FatturaElettronica':
        raise ValueError(
            f"Radice attesa 'FatturaElettronica', trovato '{_local(root.tag)}'"
        )

    header = _find(root, ['FatturaElettronicaHeader'])
    body = _find(root, ['FatturaElettronicaBody'])
    if header is None or body is None:
        raise ValueError('Struttura invalida: mancano Header o Body')

    cedente = _find(header, ['CedentePrestatore'])
    if cedente is None:
        raise ValueError('Manca CedentePrestatore')

    cedente_data = {
        'paese_iva': _text(cedente, ['DatiAnagrafici', 'IdFiscaleIVA', 'IdPaese']),
        'partita_iva': _text(cedente, ['DatiAnagrafici', 'IdFiscaleIVA', 'IdCodice']),
        'codice_fiscale': _text(cedente, ['DatiAnagrafici', 'CodiceFiscale']),
        'denominazione': _text(cedente, ['DatiAnagrafici', 'Anagrafica', 'Denominazione']),
        'nome': _text(cedente, ['DatiAnagrafici', 'Anagrafica', 'Nome']),
        'cognome': _text(cedente, ['DatiAnagrafici', 'Anagrafica', 'Cognome']),
        'indirizzo': _text(cedente, ['Sede', 'Indirizzo']),
        'cap': _text(cedente, ['Sede', 'CAP']),
        'comune': _text(cedente, ['Sede', 'Comune']),
        'provincia': _text(cedente, ['Sede', 'Provincia']),
        'nazione': _text(cedente, ['Sede', 'Nazione']),
    }

    dati_gen = _find(body, ['DatiGenerali', 'DatiGeneraliDocumento'])
    if dati_gen is None:
        raise ValueError('Manca DatiGeneraliDocumento')
    testata_data = {
        'tipo_documento': _text(dati_gen, ['TipoDocumento']) or 'TD01',
        'data': _to_date(_text(dati_gen, ['Data'])),
        'numero': _text(dati_gen, ['Numero']),
        'totale_documento': _to_decimal(_text(dati_gen, ['ImportoTotaleDocumento']) or None),
        'causale': _text(dati_gen, ['Causale']),
    }

    righe = []
    dati_beni = _find(body, ['DatiBeniServizi'])
    if dati_beni is not None:
        for linea in _findall(dati_beni, 'DettaglioLinee'):
            sconto_pct = None
            sconto_nodes = _findall(linea, 'ScontoMaggiorazione')
            if sconto_nodes:
                perc = _text(sconto_nodes[0], ['Percentuale'])
                if perc:
                    sc = _to_decimal(perc)
                    if _text(sconto_nodes[0], ['Tipo']) == 'MG':
                        sc = -sc
                    sconto_pct = sc
            righe.append({
                'numero_linea': int(_text(linea, ['NumeroLinea']) or 0) or None,
                'descrizione': _text(linea, ['Descrizione']),
                'quantita': _to_decimal(_text(linea, ['Quantita']) or '1'),
                'unita_misura': _text(linea, ['UnitaMisura']),
                'prezzo_unitario': _to_decimal(_text(linea, ['PrezzoUnitario'])),
                'prezzo_totale': _to_decimal(_text(linea, ['PrezzoTotale'])),
                'aliquota_iva': _to_decimal(_text(linea, ['AliquotaIVA'])),
                'natura': _text(linea, ['Natura']),
                'sconto_percentuale': sconto_pct,
            })

    scadenze = []
    for dp in _findall(body, 'DatiPagamento'):
        for dett in _findall(dp, 'DettaglioPagamento'):
            scadenze.append({
                'modalita_pagamento': _text(dett, ['ModalitaPagamento']),
                'data_scadenza': _to_date(_text(dett, ['DataScadenzaPagamento'])),
                'importo': _to_decimal(_text(dett, ['ImportoPagamento'])),
                'iban': _text(dett, ['IBAN']),
            })

    return {
        'cedente': cedente_data,
        'testata': testata_data,
        'righe': righe,
        'scadenze': scadenze,
    }


# ---------------------------------------------------------------------------
# Lookup helper
# ---------------------------------------------------------------------------


def _match_posizione_iva(aliquota: Decimal, natura: str):
    qs = PosizioneIva.objects.filter(aliquota=aliquota)
    if natura:
        qs = qs.filter(natura=natura)
    return qs.first()


def _resolve_fornitore(cedente: dict):
    """(fornitore, created). Match su P.IVA → CF → crea."""
    piva = (cedente.get('partita_iva') or '').strip()
    cf = (cedente.get('codice_fiscale') or '').strip()
    fornitore = None
    if piva:
        fornitore = Fornitore.objects.filter(partita_iva=piva).first()
    if fornitore is None and cf:
        fornitore = Fornitore.objects.filter(codice_fiscale=cf).first()
    if fornitore is not None:
        return fornitore, False
    full = (
        cedente.get('denominazione')
        or f"{cedente.get('nome', '')} {cedente.get('cognome', '')}".strip()
    )
    fornitore = Fornitore.objects.create(
        ragione_sociale=full or 'Fornitore da XML',
        nome=cedente.get('nome') or None,
        cognome=cedente.get('cognome') or None,
        partita_iva=piva or None,
        codice_fiscale=cf or None,
    )
    return fornitore, True


def _tipo_valid(value: str) -> str:
    choices = {c[0] for c in TestataFatturaAcquisto.TipoDocumento.choices}
    return value if value in choices else 'TD01'


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@transaction.atomic
def import_fattura_from_xml(xml_bytes: bytes, filename: str = 'fattura.xml'):
    """Crea TestataFatturaAcquisto(stato=bozza) con righe + scadenze."""
    parsed = parse_fattura_xml(xml_bytes)
    cedente = parsed['cedente']
    testata = parsed['testata']

    warnings = []
    fornitore, created = _resolve_fornitore(cedente)
    if created:
        warnings.append(
            f"Creato nuovo fornitore: {fornitore.ragione_sociale} "
            f"(P.IVA {fornitore.partita_iva or 'n/d'})"
        )

    numero_fornitore = testata['numero'] or ''
    data_fornitore = testata['data']
    existing = TestataFatturaAcquisto.objects.filter(
        fornitore=fornitore,
        numero_fornitore=numero_fornitore,
        data_fornitore=data_fornitore,
    ).first()
    if existing is not None:
        raise ValueError(
            f'Fattura già importata: fornitore {fornitore}, '
            f'numero {numero_fornitore}, data {data_fornitore}'
        )

    fattura = TestataFatturaAcquisto(
        fornitore=fornitore,
        data_documento=data_fornitore,
        data_fornitore=data_fornitore,
        numero_fornitore=numero_fornitore,
        tipo_documento=_tipo_valid(testata['tipo_documento']),
        stato=TestataFatturaAcquisto.StatoFattura.BOZZA,
        totale_documento=testata['totale_documento'],
    )
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', filename) or 'fattura.xml'
    fattura.xml_originale.save(safe, ContentFile(xml_bytes), save=False)
    fattura.save()

    for idx, r in enumerate(parsed['righe'], start=1):
        iva = _match_posizione_iva(r['aliquota_iva'], r['natura'])
        if iva is None and r['aliquota_iva'] > 0:
            warnings.append(
                f'Riga {idx}: posizione IVA {r["aliquota_iva"]}% non trovata in anagrafica'
            )
        um = _UM_MAP.get((r['unita_misura'] or '').upper())
        RigaFatturaAcquisto.objects.create(
            testata=fattura,
            numero_riga=r['numero_linea'] or idx,
            descrizione=r['descrizione'],
            quantita=r['quantita'] or Decimal('1'),
            um=um,
            prezzo_unitario=r['prezzo_unitario'],
            sconto_percentuale=r['sconto_percentuale'],
            aliquota_iva=iva,
        )

    for s in parsed['scadenze']:
        if not s['data_scadenza'] or not s['importo']:
            continue
        mp = s['modalita_pagamento']
        if mp not in _MODALITA_PAGAMENTO_VALID:
            mp = None
        ScadenzaFatturaAcquisto.objects.create(
            fattura=fattura,
            data_scadenza=s['data_scadenza'],
            importo=s['importo'],
            modalita_pagamento=mp,
            iban=s['iban'] or None,
        )

    fattura.save()  # ricalcolo totali via signal
    fattura.refresh_from_db()

    return {
        'id': fattura.id,
        'numero_protocollo': fattura.numero,
        'anno_protocollo': fattura.anno,
        'warnings': warnings,
        'fornitore_creato': created,
    }
