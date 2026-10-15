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

La PEC è quella aziendale, letta anche da persone: la ricezione non tocca
mai i flag della casella (letto/non letto) e tiene traccia delle email già
elaborate nel database (``MessaggioPecElaborato``).
"""
from __future__ import annotations

import imaplib
import logging
import re
import smtplib
import ssl
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from email import message_from_bytes
from email.message import EmailMessage
from typing import Iterable

from django.utils.timezone import now

from .p7m import estrai_xml_firmato, is_fattura_pa, nome_senza_p7m

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
    # Riferimenti per ricondurre la notifica alla fattura inviata:
    # ``nome_file`` è il NomeFile citato nella notifica (= nome del file
    # inviato, es. IT01234567890_0000A.xml), ``filename`` il nome
    # dell'allegato notifica (IT..._0000A_RC_001.xml).
    nome_file: str = ''
    filename: str = ''
    # Esito della Notifica Esito (NE): EC01 accettata / EC02 rifiutata.
    esito: str = ''
    # Chiave del messaggio sorgente nel backend (Message-ID PEC / id mock):
    # serve a registrarlo come elaborato solo dopo l'elaborazione riuscita.
    uid: str = ''
    # Casella PEC del SDI che ha spedito il messaggio (sdiNN@pec.fatturapa.it),
    # vuota se il messaggio non viene dal SDI.
    mittente: str = ''


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
    def fetch_messages(self, limit: int | None = None,
                       giorni: int | None = None) -> Iterable[SDIMessage]:
        """Ritorna i messaggi non ancora elaborati.

        ``limit`` limita il numero di messaggi sorgente (email PEC) letti:
        gli altri restano da elaborare per la run successiva. ``giorni``
        limita la ricerca ai messaggi arrivati negli ultimi N giorni (solo
        PEC). Il chiamante, dopo aver elaborato con successo tutti gli
        ``SDIMessage`` di uno stesso ``uid``, chiama
        ``mark_processed([uid, ...])``.
        """
        ...

    def mark_processed(self, uids: Iterable[str]) -> None:
        """Registra come elaborati i messaggi sorgente indicati (default: no-op)."""
        return None


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
        self._seq = 0

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
        if not message.uid:
            self._seq += 1
            message.uid = f'mock-{self._seq}'
        self._queue.append(message)

    def fetch_messages(self, limit: int | None = None,
                       giorni: int | None = None) -> Iterable[SDIMessage]:
        # Come la PEC: i messaggi restano in coda finché non vengono
        # registrati come elaborati con ``mark_processed``.
        uids: list[str] = []
        for m in self._queue:
            if m.uid not in uids:
                uids.append(m.uid)
        if limit is not None:
            uids = uids[:max(limit, 0)]
        return [m for m in self._queue if m.uid in uids]

    def mark_processed(self, uids: Iterable[str]) -> None:
        done = set(uids)
        self._queue = [m for m in self._queue if m.uid not in done]

    @property
    def sent(self):
        return list(self._sent)


# ---------------------------------------------------------------------------
# PECBackend — invia/riceve via PEC
# ---------------------------------------------------------------------------


_NOTIFICATION_TYPES = {
    'RC', 'NS', 'MC', 'NE', 'DT', 'AT', 'EC', 'NR',
}

# Casella PEC del SDI per il primo invio; dalla prima notifica il SDI
# risponde da una sua casella sdiNN@pec.fatturapa.it da usare d'ora in poi.
CASELLA_SDI_PRIMO_INVIO = 'sdi01@pec.fatturapa.it'

# Finestra di ricerca predefinita delle email PEC da elaborare.
GIORNI_FINESTRA = 30

_TIMEOUT = 60  # secondi, per SMTP e IMAP
_MESI_IMAP = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


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
        # Errore IMAP dell'ultima ``fetch_messages`` (che non lo solleva, per
        # non fermare sdi_pull): lo legge chi registra l'esito della run.
        self.ultimo_errore = ''

    # === Invio SMTP ===

    def _smtp_connect(self, timeout: int = _TIMEOUT):
        """Connessione SMTP autenticata: SSL implicito sulla 465 (default dei
        gestori PEC), altrimenti STARTTLS se ``pec_smtp_use_tls``."""
        port = self.az.pec_smtp_port or 465
        ctx = ssl.create_default_context()
        if port == 465:
            s = smtplib.SMTP_SSL(self.az.pec_smtp_host, port,
                                 context=ctx, timeout=timeout)
        else:
            s = smtplib.SMTP(self.az.pec_smtp_host, port, timeout=timeout)
        try:
            if port != 465 and self.az.pec_smtp_use_tls:
                s.starttls(context=ctx)
            s.login(self.az.pec_smtp_user, self.az.pec_smtp_password)
        except Exception:
            s.close()
            raise
        return s

    def send_xml(self, fattura, xml_bytes: bytes, filename: str) -> SDIInvioResult:
        from .invio import nome_documento
        nome = nome_documento(fattura)            # «Fattura 3/2026», «Nota di credito NC/1/2026»
        msg = EmailMessage()
        msg['Subject'] = nome
        msg['From'] = self.az.pec_mittente
        msg['To'] = self.az.pec_destinatario_sdi or CASELLA_SDI_PRIMO_INVIO
        msg.set_content(
            f'In allegato il file FatturaPA: {nome[0].lower()}{nome[1:]}.'
        )
        msg.add_attachment(
            xml_bytes,
            maintype='application',
            subtype='xml',
            filename=filename,
        )

        try:
            with self._smtp_connect() as s:
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
            descrizione=f'Email PEC inviata a {msg["To"]}, in attesa di ricevuta SDI.',
            messaggio_id_smtp=msg.get('Message-ID', ''),
        )

    # === Ricezione IMAP ===

    def _imap_configurato(self) -> bool:
        return bool(self.az.pec_imap_host and self.az.pec_imap_user
                    and self.az.pec_imap_password)

    def _imap_connect(self, timeout: int = _TIMEOUT):
        """Login IMAP e apertura della cartella in sola lettura (EXAMINE):
        nessun flag della casella può essere modificato."""
        M = imaplib.IMAP4_SSL(self.az.pec_imap_host, self.az.pec_imap_port or 993,
                              timeout=timeout)
        try:
            M.login(self.az.pec_imap_user, self.az.pec_imap_password)
            cartella = self.az.pec_imap_folder or 'INBOX'
            if ' ' in cartella and not cartella.startswith('"'):
                cartella = f'"{cartella}"'
            typ, data = M.select(cartella, readonly=True)
            if typ != 'OK':
                raise ValueError(f'Cartella IMAP {cartella} non apribile: {data}')
        except Exception:
            M.shutdown()
            raise
        return M

    def cerca_recenti(self, M, giorni: int) -> list[bytes]:
        """UID delle email arrivate negli ultimi ``giorni`` (dal più vecchio)."""
        dal = now().date() - timedelta(days=max(giorni, 0))
        typ, data = M.uid(
            'SEARCH', None, 'SINCE', f'{dal.day}-{_MESI_IMAP[dal.month - 1]}-{dal.year}',
        )
        if typ != 'OK' or not data or not data[0]:
            return []
        return sorted(data[0].split(), key=int)

    def fetch_messages(self, limit: int | None = None,
                       giorni: int | None = None) -> list[SDIMessage]:
        """Legge le email degli ultimi ``giorni`` non ancora elaborate.

        Le email elaborate sono registrate (per Message-ID) da
        ``mark_processed``, chiamato dopo l'elaborazione riuscita: un errore
        (di parsing, DB, ...) lascia l'email da elaborare e sarà ripresa alla
        run successiva. Un'email illeggibile non blocca le altre. I flag della
        casella non vengono toccati (cartella in sola lettura, ``BODY.PEEK``).
        """
        if not self._imap_configurato():
            return []
        from documenti.models import MessaggioPecElaborato

        giorni = GIORNI_FINESTRA if giorni is None else giorni
        out: list[SDIMessage] = []
        try:
            with self._imap_connect() as M:
                uids = self.cerca_recenti(M, giorni)
                if not uids:
                    return []
                _, uidvalidity = M.response('UIDVALIDITY')
                uidvalidity = (uidvalidity or [b''])[0] or b''
                chiavi = _chiavi_messaggi(M, uids, uidvalidity)
                gia = set()
                elenco = list(chiavi.values())
                for i in range(0, len(elenco), 500):
                    gia.update(MessaggioPecElaborato.objects.filter(
                        chiave__in=elenco[i:i + 500],
                    ).values_list('chiave', flat=True))
                da_fare = [u for u in uids if chiavi[u] not in gia]
                if limit is not None:
                    da_fare = da_fare[:max(limit, 0)]
                for uid in da_fare:
                    chiave = chiavi[uid]
                    try:
                        typ, msg_data = M.uid('FETCH', uid, '(BODY.PEEK[])')
                        if typ != 'OK' or not msg_data or not isinstance(msg_data[0], tuple):
                            logger.warning('SDI: fetch IMAP fallito per UID %s', uid.decode())
                            continue
                        raw = msg_data[0][1]
                        parsed = _parse_pec_message(raw)
                    except Exception:
                        logger.exception('SDI: email PEC UID %s non leggibile', uid.decode())
                        continue
                    if not parsed:
                        # Email che non viene dal SDI o senza allegati SDI (es.
                        # ricevute PEC di accettazione/consegna): la segnaliamo
                        # come vuota così il chiamante la registra come elaborata.
                        parsed = [SDIMessage(tipo='vuoto', direzione='in')]
                    for m in parsed:
                        m.uid = chiave
                    out.extend(parsed)
        except Exception as exc:
            logger.exception('Errore IMAP durante fetch_messages')
            self.ultimo_errore = f'IMAP {self.az.pec_imap_host}: {exc}'
        return out

    def mark_processed(self, uids: Iterable[str]) -> None:
        from documenti.models import MessaggioPecElaborato

        chiavi = [u for u in dict.fromkeys(uids) if u]
        MessaggioPecElaborato.objects.bulk_create(
            [MessaggioPecElaborato(chiave=c) for c in chiavi],
            ignore_conflicts=True,
        )


def _chiavi_messaggi(M, uids: list[bytes], uidvalidity: bytes) -> dict[bytes, str]:
    """UID IMAP → chiave stabile del messaggio: il Message-ID (ogni busta PEC
    ne ha uno proprio), oppure ``uid:<uidvalidity>:<uid>`` se manca."""
    message_id: dict[bytes, str] = {}
    for i in range(0, len(uids), 200):
        typ, data = M.uid(
            'FETCH', b','.join(uids[i:i + 200]).decode(),
            '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])',
        )
        if typ != 'OK':
            continue
        # Ogni risposta è (b'n (UID x BODY[...] {len}', header) seguita da
        # b')'; alcuni server mettono UID dopo il literal, nel b' UID x)'.
        in_attesa = None
        for item in data or []:
            if isinstance(item, tuple):
                m = re.search(rb'UID (\d+)', item[0])
                if m:
                    message_id[m.group(1)] = _message_id(item[1])
                    in_attesa = None
                else:
                    in_attesa = item[1]
            elif in_attesa is not None and isinstance(item, bytes):
                m = re.search(rb'UID (\d+)', item)
                if m:
                    message_id[m.group(1)] = _message_id(in_attesa)
                in_attesa = None
    return {
        uid: (message_id.get(uid) or f'uid:{uidvalidity.decode()}:{uid.decode()}')[:255]
        for uid in uids
    }


def _message_id(header: bytes) -> str:
    return str(message_from_bytes(header or b'').get('Message-ID') or '').strip()


_RE_NOTIFICA = re.compile(
    r'^IT\w+_\w+_(?P<tipo>RC|NS|MC|NE|DT|AT|EC|NR)_\w+\.xml$', re.I,
)
# sdiNN@pec.fatturapa.it, anche dentro "Per conto di: ..." codificato (=?..?Q?)
_RE_CASELLA_SDI = re.compile(
    r'(?<![A-Za-z0-9.-])sdi\d+@pec\.fatturapa\.it(?![A-Za-z0-9-]|\.[A-Za-z0-9])', re.I,
)
# Nome di un file SDI: fattura (IT<codice>_<progr>.xml[.p7m]) o notifica
_RE_NOME_FILE_SDI = re.compile(
    r'^[A-Z]{2}[A-Z0-9]+_[A-Z0-9]{1,5}(?:_[A-Z]{2}_[A-Z0-9]+)?\.xml(?:\.p7m)?$', re.I,
)


def _casella_sdi(email_msg) -> str:
    """Casella PEC del SDI che ha spedito il messaggio ('' se non viene dal SDI).

    Nella busta di trasporto PEC il gestore riporta il mittente originale in
    ``Reply-To`` e nel nome di ``From`` ("Per conto di: sdiNN@..."); il
    messaggio originale (``postacert.eml``) lo ha in ``From``. Una busta di
    anomalia (``X-Trasporto: errore``) ha un mittente non certificato.
    """
    if str(email_msg.get('X-Trasporto') or '').strip().lower() == 'errore':
        return ''
    intestazioni = [email_msg.get('Reply-To'), email_msg.get('From')]
    for part in email_msg.walk():
        if part.get_content_type() == 'message/rfc822':
            for interno in part.get_payload() or []:
                intestazioni.append(interno.get('From'))
    for valore in intestazioni:
        m = _RE_CASELLA_SDI.search(str(valore or ''))
        if m:
            return m.group(0).lower()
    return ''


def _parse_pec_message(raw: bytes) -> list[SDIMessage]:
    """Estrae notifiche SDI e fatture XML da un messaggio PEC raw.

    Le notifiche SDI hanno filename del tipo:
        IT<piva>_<progr>_<TIPO>_<id>.xml  (es. IT12345678901_00001_RC_001.xml)
    Le fatture acquisto in arrivo hanno filename:
        IT<piva>_<progr>.xml  oppure, firmate, IT<piva>_<progr>.xml.p7m

    Si considerano solo le email spedite dal SDI: file con nome da SDI in
    un'altra email (es. un cliente che inoltra una fattura) producono un
    messaggio ``ignorato``, così il chiamante lo segnala e lo registra.
    """
    msgs: list[SDIMessage] = []
    email_msg = message_from_bytes(raw)
    mittente = _casella_sdi(email_msg)
    allegati = []
    for part in email_msg.walk():
        if part.get_content_maintype() == 'multipart':
            continue
        filename = part.get_filename() or ''
        if filename.lower().endswith(('.xml', '.xml.p7m')):
            allegati.append((part, filename))

    if not mittente:
        nomi = [f for _, f in allegati if _RE_NOME_FILE_SDI.match(f)]
        if not nomi:
            return []
        descr = (
            f'Email non spedita dal SDI ({email_msg.get("From", "")}) con '
            f'allegati {", ".join(nomi)}: ignorata.'
        )
        logger.warning('SDI: %s', descr)
        return [SDIMessage(tipo='ignorato', descrizione=descr[:255], direzione='in')]

    for part, filename in allegati:
        payload = part.get_payload(decode=True) or b''
        # Notifica SDI?
        m = _RE_NOTIFICA.match(filename)
        if m:
            tipo = m.group('tipo').upper()
            info = _extract_sdi_info(payload)
            msgs.append(SDIMessage(
                tipo=tipo,
                id_trasmissione=info['id_trasmissione'],
                descrizione=info['descrizione'],
                raw=payload.decode('utf-8', errors='replace')[:4000],
                direzione='in',
                nome_file=info['nome_file'],
                filename=filename,
                esito=info['esito'],
                mittente=mittente,
            ))
            continue
        # Fattura acquisto in entrata: XML (o .xml.p7m firmato) la cui
        # radice è FatturaElettronica. Un .p7m illeggibile solleva
        # ValueError: l'email resta da elaborare e l'errore finisce nel log.
        if filename.lower().endswith('.p7m'):
            payload = estrai_xml_firmato(payload)
            filename = nome_senza_p7m(filename)
        if is_fattura_pa(payload):
            msgs.append(SDIMessage(
                tipo='altro',
                descrizione=f'Fattura in entrata: {filename}',
                raw='',
                direzione='in',
                fattura_xml=payload,
                fattura_filename=filename,
                mittente=mittente,
            ))
    return msgs


_ESITI_NE = {
    'EC01': 'Accettata dal committente (EC01)',
    'EC02': 'Rifiutata dal committente (EC02)',
}


def _extract_sdi_info(xml_bytes: bytes) -> dict:
    """Estrae da una notifica SDI: IdentificativoSdI, NomeFile, Esito
    (solo NE) e una descrizione leggibile.

    La radice delle notifiche è qualificata (namespace ``types``) mentre i
    figli non lo sono: si confronta sempre il nome locale del tag.
    """
    info = {'id_trasmissione': '', 'nome_file': '', 'esito': '', 'descrizione': ''}
    try:
        from lxml import etree
        root = etree.fromstring(xml_bytes)
    except Exception:
        return info

    def _local(el):
        tag = el.tag if isinstance(el.tag, str) else ''
        return tag.rsplit('}', 1)[-1]

    def _primo(nome, parent=root):
        for el in parent.iter():
            if _local(el) == nome and el.text and el.text.strip():
                return el.text.strip()
        return ''

    info['id_trasmissione'] = (
        _primo('IdentificativoSdI') or _primo('IdentificativoSdiDestinatario')
    )[:80]
    info['nome_file'] = _primo('NomeFile')[:255]
    esito = _primo('Esito').upper()
    info['esito'] = esito

    parti = []
    if esito:
        parti.append(_ESITI_NE.get(esito, f'Esito {esito}'))
    for tag in ('Descrizione', 'MessageId'):
        v = _primo(tag)
        if v and v not in parti:
            parti.append(v)
    descr = ' '.join(parti)
    # Lista errori (NS)
    for err in root.iter():
        if _local(err) != 'Errore':
            continue
        for tag in ('Codice', 'Descrizione'):
            v = _primo(tag, err)
            if v:
                descr = f'{descr} | {tag}: {v}'.strip(' |')
    info['descrizione'] = descr[:255]
    return info


def _extract_sdi_details(xml_bytes: bytes) -> tuple[str, str]:
    """Estrae descrizione + IdentificativoSdI da una notifica SDI."""
    info = _extract_sdi_info(xml_bytes)
    return (info['descrizione'], info['id_trasmissione'])


# ---------------------------------------------------------------------------
# Verifica della configurazione (comando sdi_verifica e Impostazioni)
# ---------------------------------------------------------------------------

# Timeout più corti di quelli dell'invio: la verifica gira anche dentro una
# richiesta web, che deve finire ben prima dei 120 s di gunicorn.
TIMEOUT_VERIFICA = 15


@dataclass
class Esito:
    sezione: str            # 'Anagrafica azienda' | 'Invio (SMTP)' | 'Ricezione (IMAP)'
    ok: bool | None         # None = solo informazione
    testo: str


def verifica_configurazione(azienda, *, giorni: int = GIORNI_FINESTRA,
                            timeout: int = TIMEOUT_VERIFICA) -> list[Esito]:
    """Controlla la configurazione SDI via PEC senza inviare nulla: dati
    dell'anagrafica, login SMTP, login IMAP, apertura della cartella in sola
    lettura ed email da elaborare. Non solleva: ogni problema è un esito KO
    (credenziali errate, server irraggiungibile, timeout)."""
    from stdnum.it import iva as stdnum_iva

    from documenti.fatt_el import _pulisci_piva
    from documenti.models import MessaggioPecElaborato

    esiti: list[Esito] = []

    def esito(sezione, ok, testo):
        esiti.append(Esito(sezione, ok, testo))

    sez = 'Anagrafica azienda'
    if azienda is None:
        esito(sez, False, 'Anagrafica azienda non configurata.')
        return esiti
    if azienda.sdi_provider == azienda.SdiProvider.PEC:
        esito(sez, True, 'Invio al SDI attivo (PEC).')
    else:
        esito(sez, False, f'Invio al SDI: «{azienda.get_sdi_provider_display()}». '
                          f'Per inviare dalla PEC scegli «PEC SMTP/IMAP».')
    piva = _pulisci_piva(azienda.partita_iva)
    if not piva:
        esito(sez, False, 'Partita IVA mancante: serve per il nome dei file e come trasmittente.')
    elif stdnum_iva.is_valid(piva):
        esito(sez, True, f'Partita IVA {piva}.')
    else:
        esito(sez, False, f'Partita IVA {azienda.partita_iva} non valida (11 cifre con '
                          f'controllo): il SDI scarterebbe ogni fattura.')
    esito(sez, None, f'Mittente {azienda.pec_mittente or "—"} → SDI '
                     f'{azienda.pec_destinatario_sdi or CASELLA_SDI_PRIMO_INVIO}')

    try:
        backend = PECBackend(azienda)
    except ValueError:
        esito('Invio (SMTP)', False, 'Mancano indirizzo PEC del mittente, server, utente '
                                     'o password SMTP.')
        return esiti

    sez = 'Invio (SMTP)'
    server = f'{azienda.pec_smtp_host}:{azienda.pec_smtp_port or 465}'
    try:
        with backend._smtp_connect(timeout=timeout):
            pass
        esito(sez, True, f'Accesso a {server} come {azienda.pec_smtp_user} riuscito.')
    except Exception as exc:
        esito(sez, False, f'{server}: {_errore_leggibile(exc)}')

    sez = 'Ricezione (IMAP)'
    if not backend._imap_configurato():
        esito(sez, False, 'Mancano server, utente o password IMAP: ricevute e fatture '
                          'di acquisto non arriveranno.')
        return esiti
    server = f'{azienda.pec_imap_host}:{azienda.pec_imap_port or 993}'
    try:
        with backend._imap_connect(timeout=timeout) as M:
            uids = backend.cerca_recenti(M, giorni)
        esito(sez, True, f'Accesso a {server} riuscito, cartella '
                         f'{azienda.pec_imap_folder or "INBOX"} aperta in sola lettura.')
        esito(sez, None, f'{len(uids)} email negli ultimi {giorni} giorni, '
                         f'{MessaggioPecElaborato.objects.count()} già elaborate in totale.')
    except Exception as exc:
        esito(sez, False, f'{server}: {_errore_leggibile(exc)}')
    return esiti


def _errore_leggibile(exc: Exception) -> str:
    """Il messaggio di un errore SMTP/IMAP/rete senza i byte grezzi del server."""
    import socket
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return 'utente o password non accettati dal server.'
    if isinstance(exc, imaplib.IMAP4.error) and 'AUTHENTICATIONFAILED' in str(exc).upper():
        return 'utente o password non accettati dal server.'
    if isinstance(exc, socket.gaierror):
        return 'server non trovato: controlla il nome.'
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return 'il server non risponde (tempo scaduto).'
    if isinstance(exc, ConnectionRefusedError):
        return 'connessione rifiutata: controlla la porta.'
    testo = exc.args[-1] if exc.args else exc
    if isinstance(testo, bytes):
        testo = testo.decode('utf-8', errors='replace')
    return str(testo or exc.__class__.__name__)[:300]


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
