"""Invio delle fatture al SDI e per email, fuori dalle view.

Li usano i pulsanti del dettaglio fattura, l'emissione delle fatture
ricorrenti (invio automatico) e possono usarli API e MCP. I problemi
«normali» (configurazione mancante, rete, indirizzo sbagliato) non sono
eccezioni: li descrive l'``Esito``, che la view mostra come messaggio.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils.timezone import now

from .models import MessaggioSdi, TestataFattura, TestataNotaCredito

# Segno sulla fattura durante l'invio: chi arriva per secondo (doppio clic,
# due schede, il timer delle ricorrenze) lo trova e si ferma. Scade dopo 10
# minuti, nel caso un invio si interrompa a metà.
IN_INVIO = 'Invio al SDI in corso…'
SCADENZA_SEGNO = timedelta(minutes=10)
MAX_TENTATIVI_SDI = 35          # il tentativo entra nel progressivo (base 36)


@dataclass
class Esito:
    ok: bool
    messaggio: str
    livello: str = 'error'      # success | warning | error, come i messages di Django


def sdi_attivo(azienda=None) -> bool:
    """True se le fatture partono dal gestionale (provider diverso da
    «disabilitato»): con un altro canale l'invio rischierebbe doppioni."""
    from anagrafiche.models import AnagraficaAzienda
    azienda = azienda if azienda is not None else AnagraficaAzienda.objects.first()
    return bool(azienda and azienda.sdi_provider != AnagraficaAzienda.SdiProvider.DISABILITATO)


def nome_documento(documento) -> str:
    """«Fattura 3/2026» o «Nota di credito NC/1/2026»: oggetto della PEC e
    messaggi."""
    from .fatt_el import numero_documento_sdi
    tipo = 'Nota di credito' if isinstance(documento, TestataNotaCredito) else 'Fattura'
    return f'{tipo} {numero_documento_sdi(documento)}'


def invia_al_sdi(fattura) -> Esito:
    """Costruisce l'XML FatturaPA di una fattura o di una nota di credito e
    lo invia con il backend configurato sull'anagrafica azienda; registra
    l'invio in ``MessaggioSdi``."""
    from anagrafiche.models import AnagraficaAzienda

    from .fatt_el import build_documento_xml, nome_file_sdi
    from .sdi import get_backend

    modello = type(fattura)
    nota = isinstance(fattura, TestataNotaCredito)
    cosa = 'La nota di credito' if nota else 'La fattura'
    if fattura.sdi_stato in (TestataFattura.SdiStato.INVIATA, TestataFattura.SdiStato.ACCETTATA,
                             TestataFattura.SdiStato.RICEVUTA_SDI):
        return Esito(False, f'{cosa} risulta già inviata al SDI.', 'warning')
    try:
        backend = get_backend()
    except ValueError as exc:  # configurazione PEC incompleta
        return Esito(False, str(exc))
    if backend is None:
        return Esito(False, "Backend SDI non configurato sull'anagrafica azienda. "
                            "Imposta sdi_provider e (se PEC) le credenziali SMTP.")

    presa = modello.objects.filter(pk=fattura.pk, sdi_stato=fattura.sdi_stato).exclude(
        Q(sdi_ultimo_messaggio=IN_INVIO, updated_at__gt=now() - SCADENZA_SEGNO),
    ).update(sdi_ultimo_messaggio=IN_INVIO, updated_at=now())
    if not presa:
        return Esito(False, f'{cosa} è già in invio al SDI: attendi l\'esito.', 'warning')
    messaggio_prima = fattura.sdi_ultimo_messaggio

    def libera():
        modello.objects.filter(pk=fattura.pk, sdi_ultimo_messaggio=IN_INVIO).update(
            sdi_ultimo_messaggio=messaggio_prima)

    # Reinvio dopo scarto/rifiuto: il SDI non accetta due volte lo stesso nome
    # file, quindi il tentativo (che entra nel progressivo) avanza. Si salva
    # solo se l'invio riesce.
    if fattura.sdi_stato in TestataFattura.SDI_STATI_REINVIO:
        if fattura.tentativo_sdi >= MAX_TENTATIVI_SDI:
            libera()
            return Esito(False, 'Raggiunto il numero massimo di reinvii al SDI per questo documento.')
        fattura.tentativo_sdi += 1

    try:
        xml_bytes = build_documento_xml(fattura)
        az = AnagraficaAzienda.objects.first()
        filename = nome_file_sdi(fattura, az.partita_iva if az else '')
        result = backend.send_xml(fattura, xml_bytes, filename)
    except ValueError as exc:  # anche FatturaElettronicaError
        libera()
        return Esito(False, f'Impossibile generare la FatturaPA: {exc}')
    except Exception:
        libera()
        raise
    if not result.success:
        modello.objects.filter(pk=fattura.pk).update(sdi_ultimo_messaggio=result.descrizione)
        return Esito(False, f'Invio fallito: {result.descrizione}')
    modello.objects.filter(pk=fattura.pk).update(
        sdi_stato=TestataFattura.SdiStato.INVIATA,
        sdi_data_invio=now(),
        sdi_id_trasmissione=result.id_trasmissione or '',
        sdi_ultimo_messaggio=result.descrizione,
        tentativo_sdi=fattura.tentativo_sdi,
    )
    MessaggioSdi.objects.create(
        **{'nota_credito' if nota else 'fattura': fattura}, tipo='invio',
        id_trasmissione=result.id_trasmissione or '',
        descrizione=result.descrizione,
        payload=xml_bytes.decode('utf-8', errors='replace')[:8000],
        direzione='out',
    )
    fattura.refresh_from_db()
    return Esito(True, f'{"Nota di credito inviata" if nota else "Fattura inviata"} al SDI '
                       f'({result.descrizione or "OK"}).', 'success')


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


def destinatario_email(fattura) -> str:
    """L'indirizzo proposto: il contatto principale del cliente, altrimenti
    la sua PEC."""
    cliente = fattura.cliente
    if cliente is None:
        return ''
    principale = cliente.contatti.filter(primary=True).exclude(email='').first()
    return (principale.email if principale else '') or (cliente.pec or '').strip()


def testi_email(fattura, azienda, totale, scadute, conto) -> dict:
    """Destinatario e testi proposti per inviare la fattura o un sollecito
    (si possono cambiare nella finestra prima dell'invio)."""
    from core_settings.templatetags.ui import euro
    cliente = fattura.cliente
    nome_azienda = azienda.ragione_sociale if azienda and azienda.ragione_sociale else ''
    numero = f'{fattura.numero}/{fattura.anno}'
    del_ = f' del {fattura.data_documento:%d/%m/%Y}' if fattura.data_documento else ''
    saluto = f'Gentile {cliente.ragione_sociale},' if cliente else 'Buongiorno,'
    firma = f'Cordiali saluti,\n{nome_azienda}' if nome_azienda else 'Cordiali saluti'
    iban = ''
    if conto is not None and conto.iban:
        iban = f'IBAN {conto.iban}' + (f' intestato a {conto.intestatario}' if conto.intestatario else '')
    righe_scad = [f'- {s.data:%d/%m/%Y}: {euro(s.importo, 2)}' for s in fattura.scadenze.all()]
    testo = '\n'.join(x for x in [
        saluto, '',
        f'in allegato la fattura n. {numero}{del_}, di {euro(totale, 2)}.',
        *(['', 'Scadenze:', *righe_scad] if righe_scad else []),
        *(['', f'Pagamento con bonifico su {iban}.'] if iban else []),
        '', firma,
    ])
    out = {'email': {'destinatario': destinatario_email(fattura),
                     'oggetto': f'Fattura {numero}' + (f' — {nome_azienda}' if nome_azienda else ''),
                     'testo': testo}}
    if scadute:
        importo = sum((s.importo_residuo for s in scadute), Decimal('0'))
        out['sollecito'] = {
            'oggetto': f'Sollecito di pagamento — fattura {numero}',
            'testo': '\n'.join(x for x in [
                saluto, '',
                f'da un nostro controllo risulta ancora da saldare la fattura n. {numero}{del_}, '
                f'scaduta il {scadute[0].data:%d/%m/%Y}, per {euro(importo, 2)}.',
                '',
                'Se ha già provveduto al pagamento, non consideri questo messaggio. '
                'Altrimenti la preghiamo di saldarla appena possibile'
                + (f' con bonifico su {iban}' if iban else '') + ', indicando il numero della fattura.',
                '', 'Restiamo a disposizione per qualsiasi chiarimento.', '', firma,
            ]),
        }
    return out


def contesto_pdf_fattura(fattura, azienda) -> dict:
    from .utils import calcola_valori, resolve_conto_corrente
    return {
        'fattura': fattura, 'azienda': azienda,
        'righe': fattura.righe.select_related('articolo', 'iva').all(),
        'scadenze': fattura.scadenze.all(),
        'totals': calcola_valori(fattura),
        'conto_corrente': resolve_conto_corrente(fattura, azienda),
    }


def invia_email(fattura, *, destinatari: str = '', oggetto: str = '', testo: str = '',
                sollecito: bool = False, request=None) -> Esito:
    """Invia la fattura (o un sollecito) per email, con il PDF e, se il
    cliente riceve fatture elettroniche e non è un sollecito, l'XML.
    ``destinatari``: uno o più indirizzi separati da virgola; vuoto = quello
    proposto (``destinatario_email``). Oggetto e testo vuoti = quelli
    proposti (``testi_email``)."""
    from django.conf import settings
    from django.core.exceptions import ValidationError
    from django.core.mail import EmailMessage
    from django.core.validators import validate_email

    from anagrafiche.models import AnagraficaAzienda
    from audit.models import Azione
    from audit.utils import log as audit_log

    from .utils import render_pdf_bytes

    indirizzi = [a for a in re.split(r'[,;\s]+', destinatari or '') if a]
    for indirizzo in indirizzi:
        try:
            validate_email(indirizzo)
        except ValidationError:
            return Esito(False, f'Indirizzo email non valido: {indirizzo}')
    if not indirizzi and destinatario_email(fattura):
        indirizzi = [destinatario_email(fattura)]
    if not indirizzi:
        return Esito(False, 'Nessun indirizzo email disponibile (contatto principale o PEC del cliente '
                            'assenti).')

    azienda = AnagraficaAzienda.objects.first()
    contesto = contesto_pdf_fattura(fattura, azienda)
    pdf_bytes = render_pdf_bytes('documenti/pdf/fattura.html', contesto, request=request)

    # L'XML solo se il cliente ha SDI/PEC configurati (a un sollecito basta il PDF)
    xml_bytes = None
    cliente = fattura.cliente
    if not sollecito and cliente and (cliente.fatturazione_elettronica_id or cliente.codice_sdi
                                      or cliente.pec):
        from .fatt_el import build_fattura_xml
        try:
            xml_bytes = build_fattura_xml(fattura)
        except ValueError as exc:  # anche FatturaElettronicaError
            return Esito(False, f'FatturaPA non generabile, email non inviata: {exc}')

    if not (oggetto and testo):
        proposti = testi_email(fattura, azienda, contesto['totals']['totale'], [],
                               contesto['conto_corrente'])['email']
        oggetto = oggetto or proposti['oggetto']
        testo = testo or proposti['testo']
    msg = EmailMessage(subject=oggetto, body=testo, from_email=settings.DEFAULT_FROM_EMAIL,
                       to=indirizzi)
    msg.attach(f'fattura_{fattura.numero}_{fattura.anno}.pdf', pdf_bytes, 'application/pdf')
    if xml_bytes:
        # Nome SDI-conforme: IT<piva>_<progressivo>.xml
        from .fatt_el import nome_file_sdi
        msg.attach(nome_file_sdi(fattura, azienda.partita_iva if azienda else ''),
                   xml_bytes, 'application/xml')
    try:
        msg.send(fail_silently=False)
    except Exception as exc:
        return Esito(False, f'Invio email fallito: {exc}')

    a = ', '.join(indirizzi)
    audit_log(Azione.EXPORT, target=fattura, request=request,
              meta={'invio': 'sollecito' if sollecito else 'email', 'a': a, 'oggetto': oggetto[:200]})
    return Esito(True, f'{"Sollecito inviato" if sollecito else "Email inviata"} a {a}.', 'success')
