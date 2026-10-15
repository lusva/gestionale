"""
Assemblaggio FatturaPA v1.2 (FPR12) per fatture cliente.

Costruzione XML con ``lxml`` in forma strutturata (niente concatenazione di
stringhe). La validazione è *lazy*: solleva ``FatturaElettronicaError`` con
messaggio leggibile quando mancano dati obbligatori.

Riferimento tecnico: schema XSD FatturaPA v1.2 — Agenzia delle Entrate.
"""
from __future__ import annotations

import re
from decimal import Decimal

from lxml import etree

NS = {
    'p': 'http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2',
    'ds': 'http://www.w3.org/2000/09/xmldsig#',
    'xsi': 'http://www.w3.org/2001/XMLSchema-instance',
}
SCHEMA_LOCATION = (
    'http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2 '
    'http://www.fatturapa.gov.it/export/fatturazione/sdi/fatturapa/v1.2/'
    'Schema_del_file_xml_FatturaPA_versione_1.2.xsd'
)


class FatturaElettronicaError(ValueError):
    """Preconditions non soddisfatte per la generazione della FatturaPA."""


def _require(condition, message):
    if not condition:
        raise FatturaElettronicaError(message)


def _fmt_num(value, decimals=2):
    """Formatta un ``Decimal`` o numero con N decimali e punto come separatore."""
    if value is None:
        return '0.00'
    q = Decimal(str(value)).quantize(Decimal('1.' + '0' * decimals))
    return format(q, 'f')


_BASE36 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'


# Limiti del progressivo (5 caratteri): le fatture hanno la prima cifra
# numerica fino a pk 466.559 (``pk*36+35 < 10*36**4``), le note di credito
# usano ``N`` + 4 caratteri fino a pk 46.655 (``pk*36+35 < 36**4``).
MAX_PK_FATTURA = (10 * 36 ** 4 - 36) // 36
MAX_PK_NOTA_CREDITO = (36 ** 4 - 36) // 36
PREFISSO_NOTA_CREDITO = 'N'


def _base36(n: int) -> str:
    cifre = ''
    while n:
        n, resto = divmod(n, 36)
        cifre = _BASE36[resto] + cifre
    return cifre or '0'


def _e_nota_credito(documento) -> bool:
    from .models import TestataNotaCredito
    return isinstance(documento, TestataNotaCredito)


def progressivo_sdi(fattura) -> str:
    """Progressivo di invio SDI: 5 caratteri alfanumerici maiuscoli.

    Il SDI rifiuta un nome file già ricevuto dallo stesso trasmittente, e
    fatture e note di credito hanno pk indipendenti: i due spazi non devono
    mai sovrapporsi.

    - Fatture: ``pk * 36 + tentativo`` in base 36 con zeri a sinistra fino a
      5 caratteri; la prima è sempre una cifra (fino a pk 466.559).
    - Note di credito: ``N`` + 4 caratteri base 36 di ``pk * 36 +
      tentativo`` (fino a pk 46.655): una lettera in testa, che nessuna
      fattura ha.

    ``tentativo`` = ``tentativo_sdi`` (i reinvii dopo scarto o rifiuto, al
    massimo 35). Univoco per documento e per invio (a differenza di
    numero/anno, che si ripete tra sezionali) e stabile tra rigenerazioni
    dello stesso invio: vale sia in ``<ProgressivoInvio>`` sia nel nome file
    ``IT<piva>_<progr>.xml`` con cui le notifiche si riconducono al documento
    (``sdi_pull.match_documento`` fa il contrario).
    """
    pk = getattr(fattura, 'pk', None)
    if not pk:
        raise FatturaElettronicaError(
            'Il documento deve essere salvato prima di generare il progressivo SDI.'
        )
    tentativo = min(int(getattr(fattura, 'tentativo_sdi', 0) or 0), 35)
    n = int(pk) * 36 + tentativo
    if _e_nota_credito(fattura):
        if pk > MAX_PK_NOTA_CREDITO:
            raise FatturaElettronicaError(
                f'Progressivo SDI fuori intervallo per la nota di credito #{pk}.')
        return PREFISSO_NOTA_CREDITO + _base36(n).rjust(4, '0')
    if pk > MAX_PK_FATTURA:
        raise FatturaElettronicaError(f'Progressivo SDI fuori intervallo per il documento #{pk}.')
    return _base36(n).rjust(5, '0')


def nome_file_sdi(fattura, piva) -> str:
    """Nome file conforme SDI: ``IT<piva>_<progressivo>.xml``.

    ``piva`` è la partita IVA del trasmittente (con o senza prefisso IT,
    spazi ammessi); se vuota si usa il segnaposto ``00000000000``.
    """
    codice = _pulisci_piva(piva or '') or '00000000000'
    return f'IT{codice}_{progressivo_sdi(fattura)}.xml'


def numero_documento_sdi(fattura) -> str:
    """Numero documento per ``<Numero>``: include il sezionale se presente
    (``A/1/2026``) così fatture con lo stesso numero su sezionali diversi
    restano distinte; senza sezionale ``1/2026``."""
    sez = (getattr(fattura, 'sezionale', '') or '').strip()
    base = f'{fattura.numero}/{fattura.anno}'
    return f'{sez}/{base}' if sez else base


def schema_fatturapa():
    """Lo schema XSD ufficiale FatturaPA da ``documenti/xsd/`` (None se i
    file non ci sono). Nessun accesso alla rete: l'import della firma
    digitale punta al file locale."""
    from pathlib import Path
    cartella = Path(__file__).resolve().parent / 'xsd'
    xsd = cartella / 'Schema_del_file_xml_FatturaPA_v1.2.2.xsd'
    if not (xsd.exists() and (cartella / 'xmldsig-core-schema.xsd').exists()):
        return None
    testo = xsd.read_bytes().replace(
        b'http://www.w3.org/TR/2002/REC-xmldsig-core-20020212/xmldsig-core-schema.xsd',
        b'xmldsig-core-schema.xsd')
    parser = etree.XMLParser(no_network=True, load_dtd=False)
    return etree.XMLSchema(etree.fromstring(testo, parser, base_url=str(xsd)))


def _SE(parent, tag, text=None):
    """Crea un SubElement con testo opzionale (omette se None/stringa vuota)."""
    el = etree.SubElement(parent, tag)
    if text is not None and text != '':
        el.text = str(text)
    return el


def destinatario_sdi(cliente) -> tuple[str, str]:
    """Codice destinatario e PEC del cliente per la FatturaPA.

    Valgono i dati di fatturazione elettronica collegati al cliente, se ci
    sono, altrimenti quelli della scheda cliente (``codice_sdi``, ``pec``,
    gli unici che il form del cliente compila). Senza codice: ``0000000``
    (la fattura arriva alla PEC o nell'area riservata del cliente)."""
    fe = cliente.fatturazione_elettronica if cliente.fatturazione_elettronica_id else None
    codici = [fe.sdi if fe else '', cliente.codice_sdi]
    sdi = next((c.strip().upper() for c in codici
                if c and c.strip() and c.strip() != '0000000'), '0000000')
    pec = next((p.strip() for p in (fe.pec if fe else '', cliente.pec) if p and p.strip()), '')
    return sdi, pec


def build_documento_xml(documento) -> bytes:
    """L'XML FatturaPA di una fattura o di una nota di credito."""
    return build_fattura_xml(documento)


def build_fattura_xml(fattura, tipo_documento: str = None) -> bytes:
    """Costruisce l'XML FatturaPA (FPR12) per una ``TestataFattura`` o una
    ``TestataNotaCredito``.

    Ritorna i bytes pronti per essere serviti o scritti su file.
    Solleva ``FatturaElettronicaError`` se mancano dati.

    Nota di credito (TD04): importi positivi come vuole la FatturaPA (il
    segno lo dà il tipo documento), ``DatiFattureCollegate`` con numero e
    data di ogni fattura stornata, niente ``DatiPagamento``. La TD08
    (semplificata) ha un altro formato (FSM10) e qui non si genera.

    Il parametro ``tipo_documento`` permette di sovrascrivere il tipo.
    """
    from anagrafiche.models import AnagraficaAzienda
    from .utils import calcola_valori

    cliente = fattura.cliente
    _require(cliente is not None, 'Manca il cliente sulla fattura.')
    _require(
        bool(cliente.indirizzo and cliente.citta),
        "Il cliente non ha un indirizzo / città: impossibile compilare "
        "la sede CessionarioCommittente.",
    )

    azienda = AnagraficaAzienda.objects.first()
    _require(azienda is not None, 'Anagrafica azienda non configurata.')
    _require(
        bool(azienda.partita_iva),
        'La partita IVA aziendale non è impostata.',
    )
    _require(
        azienda.profilo_fiscale_id is not None,
        'Il profilo fiscale aziendale non è impostato (serve il regime '
        'fiscale RF01-RF19).',
    )

    _require(
        fattura.data_documento is not None,
        'Manca la data del documento: impostala prima di generare la FatturaPA.',
    )
    righe = list(fattura.righe.select_related('iva', 'articolo').all())
    _require(bool(righe), 'La fattura non ha righe: impossibile generare la FatturaPA.')
    for i, r in enumerate(righe, start=1):
        _require(
            r.iva is not None,
            f'Riga {i} senza aliquota IVA: assegna una posizione IVA a tutte '
            f'le righe prima di generare la FatturaPA.',
        )
        _require(
            Decimal(r.iva.aliquota) != 0 or bool(r.iva.natura),
            f"Riga {i}: la posizione IVA '{r.iva.descrizione}' ha aliquota 0% "
            f"ma nessuna natura (N1-N7): il SDI la scarterebbe.",
        )

    nota = _e_nota_credito(fattura)
    totals = calcola_valori(fattura, righe)
    scadenze = [] if nota else list(fattura.scadenze.all())

    tipo_doc = tipo_documento or fattura.tipo_documento or 'TD01'
    _require(tipo_doc != 'TD08', 'Le note di credito semplificate (TD08) hanno il formato '
                                 'FatturaPA semplificato, che il gestionale non genera: usa TD04.')
    collegate = list(fattura.fatture_origine.order_by('data_documento', 'numero')) if nota else []

    # Radice con namespace p e schemaLocation
    nsmap = {'p': NS['p'], 'ds': NS['ds'], 'xsi': NS['xsi']}
    root = etree.Element(
        '{%s}FatturaElettronica' % NS['p'],
        nsmap=nsmap,
        attrib={
            'versione': 'FPR12',
            '{%s}schemaLocation' % NS['xsi']: SCHEMA_LOCATION,
        },
    )

    # --- FatturaElettronicaHeader ---
    header = etree.SubElement(root, 'FatturaElettronicaHeader')

    dati_trasm = etree.SubElement(header, 'DatiTrasmissione')
    id_trasm = etree.SubElement(dati_trasm, 'IdTrasmittente')
    _SE(id_trasm, 'IdPaese', 'IT')
    _SE(id_trasm, 'IdCodice', _pulisci_piva(azienda.partita_iva))
    _SE(dati_trasm, 'ProgressivoInvio', progressivo_sdi(fattura))
    _SE(dati_trasm, 'FormatoTrasmissione', 'FPR12')
    # Senza codice: 0000000 (PEC del cliente o area riservata), XXXXXXX per
    # i clienti esteri. La PECDestinatario si indica solo con 0000000.
    estero = (cliente.nazione or 'IT').upper() != 'IT'
    sdi, pec = destinatario_sdi(cliente)
    if estero and sdi == '0000000':
        sdi = 'XXXXXXX'
    _SE(dati_trasm, 'CodiceDestinatario', sdi)
    if pec and sdi == '0000000':
        _SE(dati_trasm, 'PECDestinatario', pec)

    # CedentePrestatore
    ced = etree.SubElement(header, 'CedentePrestatore')
    ced_da = etree.SubElement(ced, 'DatiAnagrafici')
    id_fisc = etree.SubElement(ced_da, 'IdFiscaleIVA')
    _SE(id_fisc, 'IdPaese', 'IT')
    _SE(id_fisc, 'IdCodice', _pulisci_piva(azienda.partita_iva))
    if azienda.codice_fiscale:
        _SE(ced_da, 'CodiceFiscale', azienda.codice_fiscale.upper())
    anag_ced = etree.SubElement(ced_da, 'Anagrafica')
    _SE(anag_ced, 'Denominazione', azienda.ragione_sociale)
    _SE(ced_da, 'RegimeFiscale', azienda.profilo_fiscale.regime_fiscale)
    sede_ced = etree.SubElement(ced, 'Sede')
    _SE(sede_ced, 'Indirizzo', azienda.indirizzo_legale)
    if azienda.cap_legale:
        _SE(sede_ced, 'CAP', str(azienda.cap_legale))
    if azienda.comune_legale:
        _SE(sede_ced, 'Comune', azienda.comune_legale)
    if azienda.prov_legale:
        _SE(sede_ced, 'Provincia', azienda.prov_legale.upper())
    _SE(sede_ced, 'Nazione', 'IT')

    # CessionarioCommittente
    cess = etree.SubElement(header, 'CessionarioCommittente')
    cess_da = etree.SubElement(cess, 'DatiAnagrafici')
    if cliente.partita_iva:
        id_fisc_c = etree.SubElement(cess_da, 'IdFiscaleIVA')
        _SE(id_fisc_c, 'IdPaese', cliente.nazione or 'IT')
        _SE(id_fisc_c, 'IdCodice', _pulisci_piva(cliente.partita_iva))
    if cliente.codice_fiscale:
        _SE(cess_da, 'CodiceFiscale', cliente.codice_fiscale.upper())
    anag_cess = etree.SubElement(cess_da, 'Anagrafica')
    _SE(anag_cess, 'Denominazione', cliente.ragione_sociale)
    sede_cess = etree.SubElement(cess, 'Sede')
    _SE(sede_cess, 'Indirizzo', cliente.indirizzo)
    if estero and not re.fullmatch(r'\d{5}', (cliente.cap or '').strip()):
        # Il CAP è di 5 cifre: per gli indirizzi esteri che non lo sono si
        # usa 00000.
        _SE(sede_cess, 'CAP', '00000')
    elif cliente.cap:
        _SE(sede_cess, 'CAP', cliente.cap)
    if cliente.citta:
        _SE(sede_cess, 'Comune', cliente.citta)
    if cliente.provincia:
        _SE(sede_cess, 'Provincia', cliente.provincia.upper())
    _SE(sede_cess, 'Nazione', (cliente.nazione or 'IT').upper())

    # --- FatturaElettronicaBody ---
    body = etree.SubElement(root, 'FatturaElettronicaBody')
    dati_generali = etree.SubElement(body, 'DatiGenerali')
    dati_gd = etree.SubElement(dati_generali, 'DatiGeneraliDocumento')
    _SE(dati_gd, 'TipoDocumento', tipo_doc)
    _SE(dati_gd, 'Divisa', 'EUR')
    _SE(dati_gd, 'Data', fattura.data_documento.strftime('%Y-%m-%d'))
    _SE(dati_gd, 'Numero', numero_documento_sdi(fattura))
    if totals.get('bollo'):
        bollo_el = etree.SubElement(dati_gd, 'DatiBollo')
        _SE(bollo_el, 'BolloVirtuale', 'SI')
        _SE(bollo_el, 'ImportoBollo', _fmt_num(totals['bollo']))
    cassa_info = totals.get('cassa_info')
    if totals.get('cassa_prev') and cassa_info:
        slot_cassa = next(
            (x for x in totals['iva_dettaglio'] if x['cassa']), None,
        )
        _require(
            slot_cassa is not None,
            'Cassa previdenziale senza aliquota IVA associabile.',
        )
        cp = cassa_info['cassa']
        dcp = etree.SubElement(dati_gd, 'DatiCassaPrevidenziale')
        _SE(dcp, 'TipoCassa', cp.tipo)
        _SE(dcp, 'AlCassa', _fmt_num(cp.aliquota_cassa))
        _SE(dcp, 'ImportoContributoCassa', _fmt_num(totals['cassa_prev']))
        _SE(dcp, 'ImponibileCassa', _fmt_num(cassa_info['imponibile_cassa']))
        _SE(dcp, 'AliquotaIVA', _fmt_num(slot_cassa['aliquota']))
        if slot_cassa['natura']:
            _SE(dcp, 'Natura', slot_cassa['natura'])
    _SE(dati_gd, 'ImportoTotaleDocumento', _fmt_num(totals['totale']))

    # La nota di credito cita le fatture che storna (numero e data come nel
    # loro <Numero> / <Data>).
    for f in collegate:
        dfc = etree.SubElement(dati_generali, 'DatiFattureCollegate')
        _SE(dfc, 'IdDocumento', numero_documento_sdi(f))
        if f.data_documento:
            _SE(dfc, 'Data', f.data_documento.strftime('%Y-%m-%d'))

    # Dettaglio linee
    dati_bs = etree.SubElement(body, 'DatiBeniServizi')
    for i, r in enumerate(righe, start=1):
        dl = etree.SubElement(dati_bs, 'DettaglioLinee')
        _SE(dl, 'NumeroLinea', str(i))
        descr = (
            r.descrizione_libera
            or (f'{r.articolo.codice} {r.articolo.descrizione}' if r.articolo else '')
            or '—'
        )
        _SE(dl, 'Descrizione', descr)
        _SE(dl, 'Quantita', _fmt_num(r.quantita, 8))
        if r.um:
            _SE(dl, 'UnitaMisura', r.um)
        _SE(dl, 'PrezzoUnitario', _fmt_num(r.importo_unitario, 8))
        _SE(dl, 'PrezzoTotale', _fmt_num(r.imponibile, 2))
        _SE(dl, 'AliquotaIVA', _fmt_num(r.iva.aliquota, 2))
        if r.iva.natura:
            _SE(dl, 'Natura', r.iva.natura)

    # Linee aggiuntive per aliquota: sconto di testata (negativo), spese
    # accessorie, rimborso bollo. Così per ogni aliquota la somma dei
    # PrezzoTotale (+ contributo cassa) coincide con ImponibileImporto del
    # riepilogo (controllo SDI 00422).
    n_linea = len(righe)
    descr_sconto = _descrizione_sconto(fattura)
    for iva in totals['iva_dettaglio']:
        extra = [
            ('SC', descr_sconto, -iva['sconto']),
            ('AC', 'Spese di imballo', iva['spese_imballo']),
            ('AC', 'Spese di trasporto', iva['spese_trasporto']),
            (None, 'Rimborso imposta di bollo', iva['bollo']),
        ]
        for tipo_cp, descr, importo in extra:
            if not importo:
                continue
            n_linea += 1
            dl = etree.SubElement(dati_bs, 'DettaglioLinee')
            _SE(dl, 'NumeroLinea', str(n_linea))
            if tipo_cp:
                _SE(dl, 'TipoCessionePrestazione', tipo_cp)
            _SE(dl, 'Descrizione', descr)
            _SE(dl, 'Quantita', _fmt_num(1, 8))
            _SE(dl, 'PrezzoUnitario', _fmt_num(importo, 8))
            _SE(dl, 'PrezzoTotale', _fmt_num(importo, 2))
            _SE(dl, 'AliquotaIVA', _fmt_num(iva['aliquota'], 2))
            if iva['natura']:
                _SE(dl, 'Natura', iva['natura'])

    # DatiRiepilogo per aliquota
    for iva in totals['iva_dettaglio']:
        rip = etree.SubElement(dati_bs, 'DatiRiepilogo')
        _SE(rip, 'AliquotaIVA', _fmt_num(iva['aliquota'], 2))
        if iva['natura']:
            _SE(rip, 'Natura', iva['natura'])
        _SE(rip, 'ImponibileImporto', _fmt_num(iva['imponibile']))
        _SE(rip, 'Imposta', _fmt_num(iva['imposta']))
        esig = 'I'  # default immediata
        if fattura.cliente and fattura.cliente.profilo_fiscale_id \
                and fattura.cliente.profilo_fiscale.esigibilita_iva_id:
            esig = fattura.cliente.profilo_fiscale.esigibilita_iva.tipo
        _SE(rip, 'EsigibilitaIVA', esig)
        if iva['natura'] and iva['descrizione']:
            _SE(rip, 'RiferimentoNormativo', str(iva['descrizione'])[:100])

    # Scadenze (DatiPagamento)
    if scadenze and fattura.forme_pagamento_id:
        dp = etree.SubElement(body, 'DatiPagamento')
        _SE(dp, 'CondizioniPagamento', 'TP02')  # pagamento completo
        modalita = (
            fattura.forme_pagamento.modalita_pagamento or 'MP05'
        )
        for s in scadenze:
            dett = etree.SubElement(dp, 'DettaglioPagamento')
            _SE(dett, 'ModalitaPagamento', modalita)
            _SE(dett, 'DataScadenzaPagamento', s.data.strftime('%Y-%m-%d'))
            _SE(dett, 'ImportoPagamento', _fmt_num(s.importo))
            if fattura.conto_corrente_id and fattura.conto_corrente.iban:
                _SE(dett, 'IBAN', fattura.conto_corrente.iban)

    return etree.tostring(
        root,
        xml_declaration=True,
        encoding='UTF-8',
        pretty_print=True,
    )


def _descrizione_sconto(fattura) -> str:
    """Descrizione della linea di sconto di testata (percentuale e/o importo)."""
    parti = []
    pct = getattr(fattura, 'sconto', None)
    if pct:
        parti.append(f'{_fmt_num(pct)}%')
    incond = getattr(fattura, 'sconto_incondizionato', None)
    if incond:
        parti.append(f'incondizionato {_fmt_num(incond)} EUR')
    return 'Sconto ' + ' + '.join(parti) if parti else 'Sconto'


def _pulisci_piva(piva: str) -> str:
    """Rimuove prefisso IT e whitespace/spazi, uppercase."""
    if not piva:
        return ''
    v = piva.strip().upper().replace(' ', '')
    if v.startswith('IT'):
        v = v[2:]
    return v
