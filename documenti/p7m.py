"""
Estrazione dell'XML da una fattura firmata CAdES (``.xml.p7m``).

Il file è una busta CMS/PKCS#7 ``SignedData`` che contiene l'XML originale:
qui lo si estrae senza verificare la firma (la controlla il SDI, che scarta
i file con firma non valida). Accetta DER/BER e la variante codificata in
base64; una busta dentro l'altra (firme apposte in sequenza) viene aperta
fino ad arrivare all'XML.
"""
from __future__ import annotations

import base64
import binascii
import re

from asn1crypto import cms

_MAX_BUSTE = 5
_ERRORE_FORMATO = 'Il file non è un XML FatturaPA né un .p7m leggibile.'
# Radice di una FatturaPA (con o senza prefisso di namespace)
_RE_RADICE_FATTURA = re.compile(rb'<(?:[\w.-]+:)?FatturaElettronica[\s>/]')


def is_xml(data: bytes) -> bool:
    """True se ``data`` inizia come un documento XML (BOM e spazi ammessi)."""
    return data.lstrip(b'\xef\xbb\xbf \t\r\n')[:1] == b'<'


def is_fattura_pa(xml: bytes) -> bool:
    """True se l'XML è una FatturaPA (e non, ad esempio, un file metadati
    o una notifica SDI)."""
    return bool(_RE_RADICE_FATTURA.search(xml[:4096]))


def nome_senza_p7m(filename: str) -> str:
    """``IT01234567890_abc12.xml.p7m`` → ``IT01234567890_abc12.xml``."""
    return re.sub(r'\.p7m$', '', filename or '', flags=re.I)


def estrai_xml_firmato(data: bytes) -> bytes:
    """Ritorna l'XML contenuto in un ``.p7m``; un XML in chiaro torna invariato.

    Solleva ``ValueError`` con messaggio leggibile se il file non è leggibile.
    """
    for _ in range(_MAX_BUSTE):
        if is_xml(data):
            return data
        data = _contenuto_busta(_decodifica(data))
    raise ValueError('File .p7m con troppe buste di firma annidate.')


def _decodifica(data: bytes) -> bytes:
    """DER/BER così com'è, oppure base64 (con o senza intestazioni PEM)."""
    if data[:1] == b'\x30':  # SEQUENCE: ContentInfo in DER/BER
        return data
    testo = b''.join(
        riga for riga in data.splitlines() if not riga.startswith(b'-----')
    )
    try:
        der = base64.b64decode(testo)
    except (binascii.Error, ValueError):
        raise ValueError(_ERRORE_FORMATO) from None
    if der[:1] != b'\x30':
        raise ValueError(_ERRORE_FORMATO)
    return der


def _contenuto_busta(der: bytes) -> bytes:
    try:
        info = cms.ContentInfo.load(der)
        tipo = info['content_type'].native
        contenuto = (
            info['content']['encap_content_info']['content'].native
            if tipo == 'signed_data' else None
        )
    except Exception as exc:  # asn1crypto: ValueError/TypeError su dati malformati
        raise ValueError(f'Busta .p7m non leggibile: {exc}') from exc
    if tipo != 'signed_data':
        raise ValueError(f'Il file .p7m non è una busta firmata ({tipo}).')
    if not contenuto:
        raise ValueError('Il file .p7m non contiene il documento (firma separata).')
    return contenuto
