"""
Assemblaggio FatturaPA v1.2 (FPR12) per fatture cliente.

Costruzione XML con ``lxml`` in forma strutturata (niente concatenazione di
stringhe). La validazione è *lazy*: solleva ``FatturaElettronicaError`` con
messaggio leggibile quando mancano dati obbligatori.

Riferimento tecnico: schema XSD FatturaPA v1.2 — Agenzia delle Entrate.
"""
from __future__ import annotations

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


def _SE(parent, tag, text=None):
    """Crea un SubElement con testo opzionale (omette se None/stringa vuota)."""
    el = etree.SubElement(parent, tag)
    if text is not None and text != '':
        el.text = str(text)
    return el


def build_fattura_xml(fattura, tipo_documento: str = None) -> bytes:
    """Costruisce l'XML FatturaPA per una ``TestataFattura``.

    Ritorna i bytes pronti per essere serviti o scritti su file.
    Solleva ``FatturaElettronicaError`` se mancano dati.

    Il parametro ``tipo_documento`` permette di sovrascrivere il tipo
    (utile per note di credito che passano TD04 invece di TD24).
    """
    from anagrafiche.models import AnagraficaAzienda
    from .utils import calcola_valori

    cliente = fattura.cliente
    _require(cliente is not None, 'Manca il cliente sulla fattura.')
    _require(
        cliente.fatturazione_elettronica_id is not None,
        "Il cliente non ha SDI/PEC configurati: compila i dati di "
        "fatturazione elettronica nell'anagrafica cliente.",
    )
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

    totals = calcola_valori(fattura)
    righe = list(fattura.righe.select_related('iva').all())
    scadenze = list(fattura.scadenze.all())

    tipo_doc = tipo_documento or fattura.tipo_documento or 'TD01'

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
    _SE(dati_trasm, 'ProgressivoInvio', f'{fattura.numero}{fattura.anno}')
    _SE(dati_trasm, 'FormatoTrasmissione', 'FPR12')
    sdi = (cliente.fatturazione_elettronica.sdi or '').strip() or '0000000'
    _SE(dati_trasm, 'CodiceDestinatario', sdi.upper())
    if cliente.fatturazione_elettronica.pec:
        _SE(dati_trasm, 'PECDestinatario', cliente.fatturazione_elettronica.pec)

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
    if cliente.cap:
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
    _SE(dati_gd, 'Numero', f'{fattura.numero}/{fattura.anno}')
    _SE(dati_gd, 'ImportoTotaleDocumento', _fmt_num(totals['totale']))

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
        if r.iva is not None:
            _SE(dl, 'AliquotaIVA', _fmt_num(r.iva.aliquota, 2))
            if r.iva.natura:
                _SE(dl, 'Natura', r.iva.natura)
        else:
            _SE(dl, 'AliquotaIVA', '0.00')

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


def _pulisci_piva(piva: str) -> str:
    """Rimuove prefisso IT e whitespace/spazi, uppercase."""
    if not piva:
        return ''
    v = piva.strip().upper().replace(' ', '')
    if v.startswith('IT'):
        v = v[2:]
    return v
