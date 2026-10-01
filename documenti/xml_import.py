"""
Import XML FatturaPA (ciclo passivo).

Accetta file XML in chiaro e firmati (``.xml.p7m``, da cui si estrae l'XML).
Estrae cedente, testata, righe, scadenze e crea una ``TestataFatturaAcquisto``
in stato ``bozza`` pronta per essere rivista dall'utente.

Deduplica per (fornitore, numero_fornitore, data_fornitore).
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.files.base import ContentFile
from django.db import transaction
from lxml import etree

from anagrafiche.models import Fornitore, PosizioneIva
from .p7m import estrai_xml_firmato, is_fattura_pa, nome_senza_p7m
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


def _prezzo_netto_linea(linea, prezzo_unitario: Decimal, quantita: Decimal,
                        prezzo_totale: Decimal):
    """Interpreta gli (eventuali, anche multipli) ``ScontoMaggiorazione``
    di una linea.

    Ritorna ``(prezzo_unitario, sconto_percentuale)`` da salvare sulla riga:

    - un solo sconto/maggiorazione in percentuale → prezzo invariato e
      ``sconto_percentuale`` (negativo per le maggiorazioni ``MG``);
    - più voci o voci con ``Importo`` → le voci sono applicate in cascata
      (come da specifiche FatturaPA) e si salva direttamente il prezzo
      unitario netto, senza percentuale. Se il ``PrezzoTotale`` dell'XML è
      presente, fa fede quello (evita differenze di arrotondamento).
    """
    voci = _findall(linea, 'ScontoMaggiorazione')
    if not voci:
        return prezzo_unitario, None
    if len(voci) == 1 and _text(voci[0], ['Percentuale']) \
            and not _text(voci[0], ['Importo']):
        sc = _to_decimal(_text(voci[0], ['Percentuale']))
        if _text(voci[0], ['Tipo']) == 'MG':
            sc = -sc
        return prezzo_unitario, sc

    netto = prezzo_unitario
    for v in voci:
        segno = Decimal('1') if _text(v, ['Tipo']) == 'MG' else Decimal('-1')
        perc = _text(v, ['Percentuale'])
        importo = _text(v, ['Importo'])
        if perc:
            netto = netto * (Decimal('1') + segno * _to_decimal(perc) / Decimal('100'))
        elif importo:
            # L'importo di sconto/maggiorazione è riferito al prezzo unitario
            netto = netto + segno * _to_decimal(importo)
    if prezzo_totale and quantita:
        netto = prezzo_totale / quantita
    return netto.quantize(Decimal('0.0001')), None


def _parse_body(body) -> dict[str, Any]:
    """Estrae testata, righe, scadenze e riepilogo da un FatturaElettronicaBody."""
    dati_gen = _find(body, ['DatiGenerali', 'DatiGeneraliDocumento'])
    if dati_gen is None:
        raise ValueError('Manca DatiGeneraliDocumento')
    totale_txt = _text(dati_gen, ['ImportoTotaleDocumento'])
    testata_data = {
        'tipo_documento': _text(dati_gen, ['TipoDocumento']) or 'TD01',
        'data': _to_date(_text(dati_gen, ['Data'])),
        'numero': _text(dati_gen, ['Numero']),
        'totale_documento': _to_decimal(totale_txt or None),
        # True se l'XML riporta esplicitamente ImportoTotaleDocumento
        'totale_presente': bool(totale_txt),
        'causale': _text(dati_gen, ['Causale']),
    }

    righe = []
    riepilogo = []
    dati_beni = _find(body, ['DatiBeniServizi'])
    if dati_beni is not None:
        for linea in _findall(dati_beni, 'DettaglioLinee'):
            quantita = _to_decimal(_text(linea, ['Quantita']) or '1')
            prezzo_unitario = _to_decimal(_text(linea, ['PrezzoUnitario']))
            prezzo_totale = _to_decimal(_text(linea, ['PrezzoTotale']))
            prezzo_netto, sconto_pct = _prezzo_netto_linea(
                linea, prezzo_unitario, quantita, prezzo_totale,
            )
            righe.append({
                'numero_linea': int(_text(linea, ['NumeroLinea']) or 0) or None,
                'descrizione': _text(linea, ['Descrizione']),
                'quantita': quantita,
                'unita_misura': _text(linea, ['UnitaMisura']),
                'prezzo_unitario': prezzo_netto,
                'prezzo_totale': prezzo_totale,
                'aliquota_iva': _to_decimal(_text(linea, ['AliquotaIVA'])),
                'natura': _text(linea, ['Natura']),
                'sconto_percentuale': sconto_pct,
            })
        for rip in _findall(dati_beni, 'DatiRiepilogo'):
            riepilogo.append({
                'aliquota_iva': _to_decimal(_text(rip, ['AliquotaIVA'])),
                'natura': _text(rip, ['Natura']),
                'imponibile': _to_decimal(_text(rip, ['ImponibileImporto'])),
                'imposta': _to_decimal(_text(rip, ['Imposta'])),
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
        'testata': testata_data,
        'righe': righe,
        'scadenze': scadenze,
        'riepilogo': riepilogo,
    }


def parse_fatture_xml(xml_bytes: bytes) -> list[dict[str, Any]]:
    """Come ``parse_fattura_xml`` ma ritorna un dict per ogni
    ``FatturaElettronicaBody`` (un file FatturaPA può contenere un lotto di
    più fatture dello stesso cedente verso lo stesso cessionario).
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
    bodies = _findall(root, 'FatturaElettronicaBody')
    if header is None or not bodies:
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

    out = []
    for body in bodies:
        parsed = _parse_body(body)
        parsed['cedente'] = cedente_data
        out.append(parsed)
    return out


def parse_fattura_xml(xml_bytes: bytes) -> dict[str, Any]:
    """Estrae i dati da un FatturaPA v1.2 (FPR12/FPA12).

    Solleva ``ValueError`` con messaggio leggibile se il file non è valido.
    Con più ``FatturaElettronicaBody`` ritorna il primo: per l'intero lotto
    usare ``parse_fatture_xml``.
    """
    return parse_fatture_xml(xml_bytes)[0]


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


# Tipi documento non presenti tra le scelte ma riconducibili a uno esistente
# (TD08 nota di credito semplificata → TD04 nota di credito).
_TIPO_ALIAS = {
    'TD08': 'TD04',
    'TD07': 'TD01',
    'TD09': 'TD05',
}


def _tipo_valid(value: str) -> str:
    choices = {c[0] for c in TestataFatturaAcquisto.TipoDocumento.choices}
    value = (value or '').strip().upper()
    if value in choices:
        return value
    return _TIPO_ALIAS.get(value, 'TD01')


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


class FatturaGiaImportata(ValueError):
    """Il file contiene una fattura già presente (stesso fornitore, numero, data)."""


@transaction.atomic
def import_fattura_from_xml(xml_bytes: bytes, filename: str = 'fattura.xml'):
    """Crea TestataFatturaAcquisto(stato=bozza) con righe + scadenze.

    Un file con più ``FatturaElettronicaBody`` genera una fattura per body:
    il dict ritornato descrive la prima e la chiave ``fatture`` le elenca
    tutte. Le note di credito (TD04/TD08) sono importate con tipo TD04 e
    importi positivi, come nell'XML: il segno è dato dal tipo documento.

    Un file firmato (``.xml.p7m``) viene aperto e si salva l'XML contenuto.
    """
    xml_bytes = estrai_xml_firmato(xml_bytes)
    filename = nome_senza_p7m(filename)
    lotto = parse_fatture_xml(xml_bytes)
    cedente = lotto[0]['cedente']

    warnings = []
    fornitore, created = _resolve_fornitore(cedente)
    if created:
        warnings.append(
            f"Creato nuovo fornitore: {fornitore.ragione_sociale} "
            f"(P.IVA {fornitore.partita_iva or 'n/d'})"
        )

    safe = re.sub(r'[^A-Za-z0-9._-]', '_', filename) or 'fattura.xml'
    risultati = []
    for n_body, parsed in enumerate(lotto, start=1):
        pref = f'Fattura {n_body}: ' if len(lotto) > 1 else ''
        fattura = _importa_body(parsed, fornitore, xml_bytes, safe, pref, warnings)
        risultati.append({
            'id': fattura.id,
            'numero_protocollo': fattura.numero,
            'anno_protocollo': fattura.anno,
            'numero_fornitore': fattura.numero_fornitore,
            'tipo_documento': fattura.tipo_documento,
            'totale_documento': fattura.totale_documento,
        })

    return {
        **{k: risultati[0][k] for k in ('id', 'numero_protocollo', 'anno_protocollo')},
        'warnings': warnings,
        'fornitore': str(fornitore),
        'fornitore_creato': created,
        'fatture': risultati,
    }


def _importa_body(parsed, fornitore, xml_bytes, safe_filename, pref, warnings):
    """Crea la TestataFatturaAcquisto per un singolo body del file."""
    testata = parsed['testata']
    numero_fornitore = testata['numero'] or ''
    data_fornitore = testata['data']
    existing = TestataFatturaAcquisto.objects.filter(
        fornitore=fornitore,
        numero_fornitore=numero_fornitore,
        data_fornitore=data_fornitore,
    ).first()
    if existing is not None:
        raise FatturaGiaImportata(
            f'Fattura già importata: fornitore {fornitore}, '
            f'numero {numero_fornitore}, data {data_fornitore}'
        )

    tipo_xml = (testata['tipo_documento'] or '').strip().upper()
    tipo = _tipo_valid(tipo_xml)
    if tipo != tipo_xml:
        warnings.append(
            f'{pref}tipo documento {tipo_xml or "n/d"} importato come {tipo}'
        )

    fattura = TestataFatturaAcquisto(
        fornitore=fornitore,
        data_documento=data_fornitore,
        data_fornitore=data_fornitore,
        numero_fornitore=numero_fornitore,
        tipo_documento=tipo,
        stato=TestataFatturaAcquisto.StatoFattura.BOZZA,
        totale_documento=testata['totale_documento'],
    )
    fattura.xml_originale.save(safe_filename, ContentFile(xml_bytes), save=False)
    fattura.save()

    for idx, r in enumerate(parsed['righe'], start=1):
        iva = _match_posizione_iva(r['aliquota_iva'], r['natura'])
        if iva is None and r['aliquota_iva'] > 0:
            warnings.append(
                f'{pref}riga {idx}: posizione IVA {r["aliquota_iva"]}% non trovata in anagrafica'
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

    # I signal ricalcolano imponibile/imposta/totale dalle righe (senza
    # bollo, cassa, arrotondamenti, sconti di testata...): per un documento
    # ricevuto fanno fede gli importi dell'XML. ``update`` non emette signal,
    # quindi i valori dell'XML restano fino alla prossima modifica delle righe.
    valori_xml = {}
    if parsed.get('riepilogo'):
        valori_xml['imponibile'] = sum(
            (x['imponibile'] for x in parsed['riepilogo']), Decimal('0'),
        )
        valori_xml['imposta'] = sum(
            (x['imposta'] for x in parsed['riepilogo']), Decimal('0'),
        )
    if testata.get('totale_presente'):
        valori_xml['totale_documento'] = testata['totale_documento']
    elif valori_xml:
        valori_xml['totale_documento'] = (
            valori_xml['imponibile'] + valori_xml['imposta']
        )
    if valori_xml:
        TestataFatturaAcquisto.objects.filter(pk=fattura.pk).update(**valori_xml)
    fattura.refresh_from_db()
    return fattura


# ---------------------------------------------------------------------------
# Import da upload (più file / zip)
# ---------------------------------------------------------------------------

_MAX_FILE = 10 * 1024 * 1024          # singola fattura
_MAX_VOCI_ZIP = 1000
_MAX_ZIP_ESTRATTO = 200 * 1024 * 1024  # totale estratto da uno zip


def importa_file(nome: str, dati: bytes, etichetta: str = '',
                 _dentro_zip: bool = False) -> list[dict]:
    """Importa le fatture contenute in un file caricato: XML, ``.xml.p7m`` o
    zip (lo scaricamento multiplo di Aruba o del portale dell'Agenzia).

    Ritorna un esito per file: ``{'file', 'esito', 'messaggio', 'result'}``
    con ``esito`` = ``importata`` | ``gia_presente`` | ``ignorato`` |
    ``errore``; ``file`` è ``etichetta`` se data (es. ``archivio.zip › nome``).
    Ogni fattura ha la sua transazione: un errore non blocca le altre. I file
    che non sono fatture (metadati, notifiche, PDF) sono ignorati.
    """
    def esito(tipo, messaggio='', result=None):
        return {'file': etichetta or nome, 'esito': tipo,
                'messaggio': messaggio, 'result': result}

    basso = nome.lower()
    if basso.endswith('.zip'):
        if _dentro_zip:
            return [esito('ignorato', 'zip dentro uno zip: estrailo e caricalo a parte')]
        return _importa_zip(nome, dati)
    if not basso.endswith(('.xml', '.p7m')):
        return [esito('ignorato', 'non è un file .xml o .p7m')]
    if len(dati) > _MAX_FILE:
        return [esito('errore', 'file troppo grande (max 10 MB)')]
    try:
        xml = estrai_xml_firmato(dati)
    except ValueError as exc:
        return [esito('errore', str(exc))]
    if not is_fattura_pa(xml):
        return [esito('ignorato', 'non è una fattura (file metadati o notifica)')]
    try:
        result = import_fattura_from_xml(xml, filename=nome.rsplit('/', 1)[-1])
    except FatturaGiaImportata as exc:
        return [esito('gia_presente', str(exc))]
    except ValueError as exc:
        return [esito('errore', str(exc))]
    return [esito('importata', result=result)]


def _importa_zip(nome: str, dati: bytes) -> list[dict]:
    def errore(messaggio, file=nome):
        return {'file': file, 'esito': 'errore', 'messaggio': messaggio, 'result': None}

    try:
        archivio = zipfile.ZipFile(io.BytesIO(dati))
    except zipfile.BadZipFile:
        return [errore('zip non valido')]
    voci = [
        v for v in archivio.infolist()
        if not v.is_dir() and not v.filename.startswith('__MACOSX/')
        and not v.filename.rsplit('/', 1)[-1].startswith('.')
    ]
    if len(voci) > _MAX_VOCI_ZIP:
        return [errore(f'troppi file nello zip (max {_MAX_VOCI_ZIP})')]
    esiti, estratto = [], 0
    for voce in voci:
        etichetta = f'{nome} › {voce.filename}'
        if not voce.filename.lower().endswith(('.xml', '.p7m')):
            # PDF, zip annidati, ...: esito "ignorato" senza estrarli
            esiti.extend(importa_file(voce.filename, b'', etichetta, _dentro_zip=True))
            continue
        # file_size limita anche la decompressione (zipfile non legge oltre)
        if voce.file_size > _MAX_FILE:
            esiti.append(errore('file troppo grande (max 10 MB)', etichetta))
            continue
        estratto += voce.file_size
        if estratto > _MAX_ZIP_ESTRATTO:
            esiti.append(errore('zip troppo grande: dividilo in più parti', etichetta))
            break
        try:
            contenuto = archivio.read(voce)
        except Exception as exc:  # voce corrotta o cifrata
            esiti.append(errore(f'voce dello zip illeggibile: {exc}', etichetta))
            continue
        esiti.extend(importa_file(voce.filename, contenuto, etichetta, _dentro_zip=True))
    return esiti
