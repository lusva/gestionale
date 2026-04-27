"""
Integrazione Sistema di Interscambio (SDI) tramite PEC.

Architettura pluggabile:
- ``SDIBackend`` (ABC) definisce ``send_xml()`` e ``fetch_messages()``
- ``MockBackend`` per dev/test: simula invio + restituisce notifiche fake
- ``PECBackend`` per uso reale: SMTP+IMAP verso la PEC dell'azienda

La modalità ``PEC`` è il canale legale storico per inviare fatture al SDI:
si invia un'email PEC contenente l'XML come allegato a
``sdi01@pec.fatturapa.it`` (o casella indicata dal SDI). Le notifiche
arrivano sulla stessa PEC dell'azienda con allegato XML strutturato.
"""
from __future__ import annotations

import imaplib
import logging
import re
import smtplib
import ssl
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from email import message_from_bytes
from email.message import EmailMessage
from typing import Iterable

from django.utils.timezone import now

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


@dataclass
class SDIMessage:
    """Messaggio ricevuto dal SDI o inviato all'SDI."""
    tipo: str  # 'invio' | 'RC' | 'NS' | 'MC' | 'NE' | 'DT' | 'AT' | 'altro'
    id_trasmissione: str = ''
    descrizione: str = ''
    raw: str = ''
    direzione: str = 'in'  # 'in' | 'out'
    # Se è un XML di fattura acquisto in entrata, contiene i bytes:
    fattura_xml: bytes | None = None
    fattura_filename: str = ''


@dataclass
class SDIInvioResult:
    success: bool
    id_trasmissione: str = ''
    descrizione: str = ''
    messaggio_id_smtp: str = ''


# ---------------------------------------------------------------------------
# Backend astratto
# ---------------------------------------------------------------------------


class SDIBackend(ABC):
    """Interfaccia comune ai backend di invio/ricezione SDI."""

    @abstractmethod
    def send_xml(self, fattura, xml_bytes: bytes, filename: str) -> SDIInvioResult:
        ...

    @abstractmethod
    def fetch_messages(self) -> Iterable[SDIMessage]:
        ...


# ---------------------------------------------------------------------------
# MockBackend — per dev/test
# ---------------------------------------------------------------------------


class MockBackend(SDIBackend):
    """Backend in-memory: registra invii e restituisce notifiche pre-popolate.

    Utile in test e per dimostrazioni senza una PEC reale. Le notifiche
    in coda possono essere pre-impostate via ``queue_message()``.
    """

    def __init__(self):
        self._sent: list[dict] = []
        self._queue: list[SDIMessage] = []

    def send_xml(self, fattura, xml_bytes: bytes, filename: str) -> SDIInvioResult:
        id_tr = f'MOCK-{fattura.numero}{fattura.anno}-{int(now().timestamp())}'
        self._sent.append({
            'fattura_id': fattura.pk, 'filename': filename,
            'xml_size': len(xml_bytes), 'id_trasmissione': id_tr,
        })
        return SDIInvioResult(
            success=True, id_trasmissione=id_tr,
            descrizione='[Mock] Invio simulato OK',
            messaggio_id_smtp=f'<{id_tr}@mock>',
        )

    def queue_message(self, message: SDIMessage) -> None:
        self._queue.append(message)

    def fetch_messages(self) -> Iterable[SDIMessage]:
        out = list(self._queue)
        self._queue.clear()
        return out

    @property
    def sent(self):
        return list(self._sent)


# ---------------------------------------------------------------------------
# PECBackend — invia/riceve via PEC
# ---------------------------------------------------------------------------


_NOTIFICATION_TYPES = {
    'RC', 'NS', 'MC', 'NE', 'DT', 'AT', 'EC', 'NR',
}


class PECBackend(SDIBackend):
    """Invio via SMTP PEC, ricezione notifiche via IMAP."""

    def __init__(self, azienda):
        self.az = azienda
        if not (azienda.pec_mittente and azienda.pec_smtp_host
                and azienda.pec_smtp_user and azienda.pec_smtp_password):
            raise ValueError(
                "Configurazione PEC SMTP incompleta: imposta pec_mittente, "
                "pec_smtp_host/user/password sull'AnagraficaAzienda."
            )

    def send_xml(self, fattura, xml_bytes: bytes, filename: str) -> SDIInvioResult:
        msg = EmailMessage()
        msg['Subject'] = f'Fattura {fattura.numero}/{fattura.anno}'
        msg['From'] = self.az.pec_mittente
        msg['To'] = self.az.pec_destinatario_sdi or 'sdi01@pec.fatturapa.it'
        msg.set_content(
            f'In allegato la fattura elettronica {fattura.numero}/{fattura.anno}.'
        )
        msg.add_attachment(
            xml_bytes,
            maintype='application',
            subtype='xml',
            filename=filename,
        )

        port = self.az.pec_smtp_port or (465 if not self.az.pec_smtp_use_tls else 587)
        try:
            if port == 465:
                ctx = ssl.create_default_context()
                with smtplib.SMTP_SSL(self.az.pec_smtp_host, port, context=ctx) as s:
                    s.login(self.az.pec_smtp_user, self.az.pec_smtp_password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(self.az.pec_smtp_host, port) as s:
                    if self.az.pec_smtp_use_tls:
                        s.starttls(context=ssl.create_default_context())
                    s.login(self.az.pec_smtp_user, self.az.pec_smtp_password)
                    s.send_message(msg)
        except Exception as exc:
            logger.exception('PEC SDI invio fallito')
            return SDIInvioResult(
                success=False,
                descrizione=f'Errore invio PEC: {exc}',
            )

        return SDIInvioResult(
            success=True,
            id_trasmissione='',  # Verrà popolato dalla notifica RC asincrona
            descrizione='Email PEC inviata, in attesa di ricevuta SDI.',
            messaggio_id_smtp=msg.get('Message-ID', ''),
        )

    # === Ricezione IMAP ===

    def fetch_messages(self) -> list[SDIMessage]:
        if not (self.az.pec_imap_host and self.az.pec_imap_user
                and self.az.pec_imap_password):
            return []
        out: list[SDIMessage] = []
        host = self.az.pec_imap_host
        port = self.az.pec_imap_port or 993
        try:
            with imaplib.IMAP4_SSL(host, port) as M:
                M.login(self.az.pec_imap_user, self.az.pec_imap_password)
                M.select(self.az.pec_imap_folder or 'INBOX')
                typ, data = M.search(None, 'UNSEEN')
                if typ != 'OK':
                    return []
                for num in data[0].split():
                    typ, msg_data = M.fetch(num, '(RFC822)')
                    if typ != 'OK':
                        continue
                    raw = msg_data[0][1]
                    parsed = _parse_pec_message(raw)
                    out.extend(parsed)
                    # Marca come letto solo dopo aver processato
                    M.store(num, '+FLAGS', '\\Seen')
        except Exception:
            logger.exception('Errore IMAP durante fetch_messages')
            return out
        return out


def _parse_pec_message(raw: bytes) -> list[SDIMessage]:
    """Estrae notifiche SDI e fatture XML da un messaggio PEC raw.

    Le notifiche SDI hanno filename del tipo:
        IT<piva>_<progr>_<TIPO>_<id>.xml  (es. IT12345678901_00001_RC_001.xml)
    Le fatture acquisto in arrivo hanno filename:
        IT<piva>_<progr>.xml
    """
    msgs: list[SDIMessage] = []
    email_msg = message_from_bytes(raw)
    for part in email_msg.walk():
        if part.get_content_maintype() == 'multipart':
            continue
        filename = part.get_filename() or ''
        if not filename.lower().endswith(('.xml', '.zip')):
            continue
        payload = part.get_payload(decode=True) or b''
        # Notifica SDI?
        m = re.match(
            r'^IT\w+_\w+_(?P<tipo>RC|NS|MC|NE|DT|AT|EC|NR)_\w+\.xml$',
            filename, re.I,
        )
        if m:
            tipo = m.group('tipo').upper()
            descrizione, id_tr = _extract_sdi_details(payload)
            msgs.append(SDIMessage(
                tipo=tipo,
                id_trasmissione=id_tr,
                descrizione=descrizione,
                raw=payload.decode('utf-8', errors='replace')[:4000],
                direzione='in',
            ))
            continue
        # Fattura acquisto in entrata: file XML che inizia con FatturaElettronica
        if filename.lower().endswith('.xml'):
            head = payload[:200].lower()
            if b'fatturaelettronica' in head:
                msgs.append(SDIMessage(
                    tipo='altro',
                    descrizione=f'Fattura in entrata: {filename}',
                    raw='',
                    direzione='in',
                    fattura_xml=payload,
                    fattura_filename=filename,
                ))
    return msgs


def _extract_sdi_details(xml_bytes: bytes) -> tuple[str, str]:
    """Estrae descrizione + IdentificativoSdI da una notifica SDI."""
    try:
        from lxml import etree
        root = etree.fromstring(xml_bytes)
    except Exception:
        return ('', '')
    id_tr = ''
    descr = ''
    # I tag SDI variano per tipo; provo i più comuni:
    for tag in ('IdentificativoSdI', 'IdentificativoSdiDestinatario'):
        el = root.find(f'.//{tag}')
        if el is not None and el.text:
            id_tr = el.text.strip()
            break
    for tag in ('Descrizione', 'MessageId', 'Esito'):
        el = root.find(f'.//{tag}')
        if el is not None and el.text:
            descr = (descr + ' ' + el.text.strip()).strip()
    # Lista errori (NS)
    for err in root.findall('.//Errore'):
        for tag in ('Codice', 'Descrizione'):
            sub = err.find(tag)
            if sub is not None and sub.text:
                descr = f'{descr} | {tag}: {sub.text.strip()}'.strip(' |')
    return (descr[:255], id_tr[:80])


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_backend(azienda=None) -> SDIBackend | None:
    """Costruisce il backend SDI dall'AnagraficaAzienda configurata.

    Ritorna ``None`` se ``sdi_provider`` è ``disabilitato`` o l'anagrafica
    non è configurata. Importa al volo da ``anagrafiche.models``.
    """
    if azienda is None:
        from anagrafiche.models import AnagraficaAzienda
        azienda = AnagraficaAzienda.objects.first()
    if azienda is None:
        return None
    provider = azienda.sdi_provider
    if provider == 'mock':
        # Singleton per session di processo
        if not hasattr(get_backend, '_mock'):
            get_backend._mock = MockBackend()
        return get_backend._mock
    if provider == 'pec':
        return PECBackend(azienda)
    return None
