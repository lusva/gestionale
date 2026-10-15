"""
Helper comuni a tutti i documenti: calcolo totali, generazione PDF
(WeasyPrint), auto-generazione scadenze da una forma pagamento.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote

from django.http import HttpResponse
from django.template.loader import render_to_string


_Q = Decimal('0.01')

# Soglia oltre la quale le operazioni esenti/escluse da IVA scontano
# l'imposta di bollo (art. 6 tariffa DPR 642/72) e importo del bollo virtuale.
SOGLIA_BOLLO = Decimal('77.47')
IMPORTO_BOLLO = Decimal('2.00')
# Natura con cui si addebita al cliente il rimborso del bollo (esclusa ex art. 15).
NATURA_BOLLO = 'N1'


def _ripartisci(importo: Decimal, pesi: list) -> list:
    """Ripartisce ``importo`` (già al centesimo) in proporzione ai ``pesi``.

    Ogni quota è arrotondata al centesimo e il resto dell'arrotondamento
    va sull'ultima quota, così la somma coincide esattamente con ``importo``.
    Con pesi tutti nulli l'importo va interamente sull'ultima quota.
    """
    if not pesi:
        return []
    totale_pesi = sum(pesi, Decimal('0'))
    quote = []
    for p in pesi[:-1]:
        if totale_pesi:
            quote.append((importo * p / totale_pesi).quantize(_Q))
        else:
            quote.append(Decimal('0.00'))
    quote.append(importo - sum(quote, Decimal('0')))
    return quote


def _chiave_iva(iva) -> str:
    return f'{Decimal(iva.aliquota).quantize(_Q)}|{iva.natura or ""}'


_DA_CARICARE = object()


def azienda_per_calcoli():
    """L'anagrafica azienda con profilo fiscale e cassa previdenziale già
    caricati: da passare a ``calcola_valori`` nei cicli su molti documenti,
    che altrimenti la rileggerebbe (2-4 query) per ognuno."""
    from anagrafiche.models import AnagraficaAzienda
    return AnagraficaAzienda.objects.select_related(
        'profilo_fiscale__cassa_previdenziale__codice_iva').first()


def calcola_valori(testata, righe=None, azienda=_DA_CARICARE):
    """Calcola i totali di una testata a partire dalle righe.

    ``righe``: le righe da usare al posto di quelle salvate (anche non
    salvate, con ``iva`` già risolta): serve all'anteprima dell'API, che
    calcola i totali di un documento senza scriverlo. ``azienda``: quella di
    ``azienda_per_calcoli()``, se la si ha già (cassa previdenziale).

    Ritorna dict con: ``imponibile``, ``iva_dettaglio`` (lista di
    {aliquota, imponibile, imposta}), ``imposta_totale``, ``sconto``
    (importo già scalato), ``totale``.

    Tutti gli importi sono calcolati al centesimo per aliquota, nello stesso
    modo in cui vengono esposti nella FatturaPA (``fatt_el``): per ogni
    aliquota l'``imponibile`` del riepilogo è la somma di

    - righe (``importo_unitario * quantita`` arrotondato al centesimo)
    - quota dello sconto di testata (percentuale + incondizionato), negativa
    - quota delle spese di imballo / trasporto (soggette all'IVA delle
      operazioni cui sono accessorie, art. 12 DPR 633/72, ripartite in
      proporzione)
    - contributo cassa previdenziale (sull'aliquota ``codice_iva`` della
      cassa o, in mancanza, su quella prevalente)
    - rimborso bollo (natura N1) se dovuto

    e l'imposta è ricalcolata sull'imponibile arrotondato. Le chiavi extra
    di ogni voce di ``iva_dettaglio`` (``imponibile_righe``, ``sconto``,
    ``spese_imballo``, ``spese_trasporto``, ``cassa``, ``bollo``) servono a
    ``fatt_el`` per generare le linee corrispondenti.

    La cassa previdenziale, se configurata sull'AnagraficaAzienda, viene
    applicata sull'imponibile (percentuale_imponibile) e aggiunta al totale.

    Bollo: se almeno una riga ha una posizione IVA con ``bollo=True`` e
    l'imponibile delle operazioni senza IVA (aliquota 0) supera 77,47 €, si
    addebitano 2,00 € (``bollo`` nel dict) inclusi nel totale.
    """
    if righe is None:
        righe = list(testata.righe.select_related('iva').all())

    def _nuovo_slot(aliquota, natura, descrizione):
        return {
            'aliquota': Decimal(aliquota).quantize(_Q),
            'natura': natura or '',
            'descrizione': descrizione,
            'imponibile_righe': Decimal('0.00'),
            'sconto': Decimal('0.00'),
            'spese_imballo': Decimal('0.00'),
            'spese_trasporto': Decimal('0.00'),
            'cassa': Decimal('0.00'),
            'bollo': Decimal('0.00'),
        }

    imponibile = Decimal('0.00')
    # Chiave ``None``: righe senza posizione IVA (non esposte nel riepilogo
    # IVA ma conteggiate nei totali, come in passato).
    iva_map: dict = {}
    bollo_richiesto = False
    for r in righe:
        row_imp = (
            Decimal(str(r.importo_unitario)) * Decimal(str(r.quantita))
        ).quantize(_Q)
        imponibile += row_imp
        if r.iva is not None:
            key = _chiave_iva(r.iva)
            slot = iva_map.setdefault(
                key, _nuovo_slot(r.iva.aliquota, r.iva.natura, r.iva.descrizione),
            )
            if getattr(r.iva, 'bollo', False):
                bollo_richiesto = True
        else:
            slot = iva_map.setdefault(None, _nuovo_slot(0, '', ''))
        slot['imponibile_righe'] += row_imp

    chiavi = list(iva_map.keys())
    chiavi_iva = [k for k in chiavi if k is not None]

    # Sconto sulla testata (percentuale + incondizionato), ripartito per
    # aliquota in proporzione all'imponibile delle righe.
    sconto_pct = Decimal(str(getattr(testata, 'sconto', None) or 0))
    sconto_incond = Decimal(str(getattr(testata, 'sconto_incondizionato', None) or 0))
    sconto_value = (
        imponibile * sconto_pct / Decimal('100') + sconto_incond
    ).quantize(_Q)
    imponibile_scontato = imponibile - sconto_value
    for k, quota in zip(chiavi, _ripartisci(
            sconto_value, [iva_map[k]['imponibile_righe'] for k in chiavi])):
        iva_map[k]['sconto'] = quota

    # Spese accessorie: seguono l'IVA delle operazioni principali, ripartite
    # in proporzione all'imponibile scontato di ciascuna aliquota.
    spese_imballo = Decimal(str(getattr(testata, 'spese_imballo', None) or 0)).quantize(_Q)
    spese_trasporto = Decimal(str(getattr(testata, 'spese_trasporto', None) or 0)).quantize(_Q)
    spese = spese_imballo + spese_trasporto
    chiavi_spese = chiavi_iva or chiavi
    pesi_spese = [
        iva_map[k]['imponibile_righe'] - iva_map[k]['sconto'] for k in chiavi_spese
    ]
    if spese and not chiavi_spese:
        iva_map[None] = _nuovo_slot(0, '', '')
        chiavi_spese = [None]
        pesi_spese = [Decimal('0')]
    for campo, importo in (('spese_imballo', spese_imballo),
                           ('spese_trasporto', spese_trasporto)):
        if not importo:
            continue
        for k, quota in zip(chiavi_spese, _ripartisci(importo, pesi_spese)):
            iva_map[k][campo] = quota

    # Cassa previdenziale (dalla AnagraficaAzienda.profilo_fiscale se presente)
    cassa = Decimal('0.00')
    cassa_info = None
    try:
        if azienda is _DA_CARICARE:
            azienda = azienda_per_calcoli()
        if azienda and azienda.profilo_fiscale_id:
            cp = azienda.profilo_fiscale.cassa_previdenziale
            if cp and cp.attiva:
                base = (
                    imponibile_scontato * (cp.percentuale_imponibile / Decimal('100'))
                ).quantize(_Q)
                cassa = (base * cp.aliquota_cassa / Decimal('100')).quantize(_Q)
                cassa_info = {'cassa': cp, 'imponibile_cassa': base}
    except Exception:
        cassa = Decimal('0.00')
        cassa_info = None
    if cassa:
        chiave_cassa = None
        cp = cassa_info['cassa']
        if getattr(cp, 'codice_iva_id', None):
            iva_cassa = cp.codice_iva
            chiave_cassa = _chiave_iva(iva_cassa)
            iva_map.setdefault(chiave_cassa, _nuovo_slot(
                iva_cassa.aliquota, iva_cassa.natura, iva_cassa.descrizione,
            ))
        elif chiavi_iva:
            # Aliquota prevalente (imponibile scontato più alto)
            chiave_cassa = max(
                chiavi_iva,
                key=lambda k: iva_map[k]['imponibile_righe'] - iva_map[k]['sconto'],
            )
        else:
            iva_map.setdefault(None, _nuovo_slot(0, '', ''))
        iva_map[chiave_cassa]['cassa'] = cassa
        cassa_info['chiave_iva'] = chiave_cassa

    def _imp_slot(s):
        return (s['imponibile_righe'] - s['sconto'] + s['spese_imballo']
                + s['spese_trasporto'] + s['cassa'] + s['bollo'])

    # Bollo: dovuto se una riga è su posizione IVA "con bollo" e le
    # operazioni senza IVA superano la soglia di legge.
    bollo = Decimal('0.00')
    if bollo_richiesto:
        imponibile_esente = sum(
            (_imp_slot(iva_map[k]) for k in iva_map
             if k is not None and iva_map[k]['aliquota'] == 0),
            Decimal('0'),
        )
        if imponibile_esente > SOGLIA_BOLLO:
            bollo = IMPORTO_BOLLO
            chiave_bollo = f'{Decimal("0.00")}|{NATURA_BOLLO}'
            iva_map.setdefault(chiave_bollo, _nuovo_slot(
                0, NATURA_BOLLO, 'Escluse ex art. 15 DPR 633/72',
            ))
            iva_map[chiave_bollo]['bollo'] = bollo

    imposta_totale = Decimal('0.00')
    iva_dettaglio = []
    for k, slot in iva_map.items():
        if k is None:
            continue
        imp = _imp_slot(slot)
        imposta = (imp * slot['aliquota'] / Decimal('100')).quantize(_Q)
        imposta_totale += imposta
        iva_dettaglio.append({
            **slot,
            'imponibile': imp,
            'imposta': imposta,
        })

    totale = imponibile_scontato + spese + cassa + bollo + imposta_totale

    return {
        'imponibile_lordo': imponibile.quantize(_Q),
        'sconto': sconto_value,
        'imponibile': imponibile_scontato.quantize(_Q),
        'spese': spese,
        'spese_imballo': spese_imballo,
        'spese_trasporto': spese_trasporto,
        'cassa_prev': cassa,
        'cassa_info': cassa_info,
        'bollo': bollo,
        'iva_dettaglio': iva_dettaglio,
        'imposta_totale': imposta_totale,
        'totale': totale.quantize(_Q),
    }


def genera_scadenze_da_forma_pagamento(testata, totale=None):
    """Auto-genera scadenze in base a ``testata.forme_pagamento``.

    Presuppone una ``data_documento`` valorizzata e una lista di ``Scadenza``
    collegate alla ``FormePagamento``. Non salva automaticamente la
    ``ScadenzaFattura``: ritorna la lista di dict che il chiamante può
    persistere o presentare in UI.
    """
    if not testata.forme_pagamento_id or not testata.data_documento:
        return []
    if totale is None:
        totale = calcola_valori(testata)['totale']
    base = testata.data_documento
    totale = Decimal(str(totale)).quantize(_Q)
    regole = list(testata.forme_pagamento.scadenze.all())
    scadenze = []
    for idx, s in enumerate(regole):
        giorni = int(s.numero_giorni or 0)
        data = base + timedelta(days=giorni)
        if s.fine_mese:
            if data.month == 12:
                data = date(data.year + 1, 1, 1) - timedelta(days=1)
            else:
                data = date(data.year, data.month + 1, 1) - timedelta(days=1)
            if s.numero_giorni_fm:
                data += timedelta(days=int(s.numero_giorni_fm))
        importo = (totale * Decimal(s.percentuale) / Decimal('100')).quantize(_Q)
        scadenze.append({'data': data, 'importo': importo})
    # Il resto dell'arrotondamento va sull'ultima rata, così la somma delle
    # scadenze coincide al centesimo con il totale (se le percentuali
    # coprono il 100%).
    if scadenze:
        tot_perc = sum((Decimal(s.percentuale) for s in regole), Decimal('0'))
        if tot_perc == Decimal('100'):
            gia_assegnato = sum(
                (x['importo'] for x in scadenze[:-1]), Decimal('0'),
            )
            scadenze[-1]['importo'] = totale - gia_assegnato
    return scadenze


def resolve_conto_corrente(fattura, azienda):
    """Risolve il conto corrente da mostrare in fattura.

    Priorità: conto associato alla fattura, poi conto ``default=True``
    sull'azienda, infine il primo conto disponibile.
    """
    if getattr(fattura, 'conto_corrente_id', None):
        return fattura.conto_corrente
    if azienda is None:
        return None
    return (
        azienda.conti_correnti.filter(default=True).first()
        or azienda.conti_correnti.first()
    )


def firma_pdf_bytes(pdf_bytes: bytes) -> bytes | None:
    """Firma un PDF in PAdES usando il certificato configurato sull'AnagraficaAzienda.

    Ritorna i bytes del PDF firmato. Se ``AnagraficaAzienda`` non ha un
    certificato_p12 caricato (o la password non è impostata) ritorna ``None``
    senza errori — il chiamante decide se restituire il PDF non firmato o
    sollevare un errore visibile.
    """
    from anagrafiche.models import AnagraficaAzienda

    az = AnagraficaAzienda.objects.first()
    if az is None or not az.certificato_p12:
        return None
    if not az.certificato_password:
        return None

    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers
    from pyhanko_certvalidator import ValidationContext

    cert_path = az.certificato_p12.path
    signer = signers.SimpleSigner.load_pkcs12(
        pfx_file=cert_path,
        passphrase=az.certificato_password.encode('utf-8'),
    )
    if signer is None:
        return None

    src_buffer = BytesIO(pdf_bytes)
    out_buffer = BytesIO()
    writer = IncrementalPdfFileWriter(src_buffer)
    # Per certificati self-signed o emessi da CA non in trust store di sistema,
    # mettiamo il certificato del firmatario stesso come trust root: pyHanko
    # bypassa la validazione di catena online (allow_fetching=False) e
    # accetta la firma. In produzione con cert qualificato CA-attestato, la
    # CA root va aggiunta manualmente nel trust_store di sistema o caricata qui.
    vc = ValidationContext(
        trust_roots=[signer.signing_cert],
        allow_fetching=False,
    )
    meta = signers.PdfSignatureMetadata(
        field_name='Signature1',
        reason=az.firma_motivo or 'Documento emesso elettronicamente',
        location=az.comune_legale or '',
        validation_context=vc,
        # Non vincoliamo i key_usage (alcuni certificati di test/CA non hanno
        # ``non_repudiation`` impostato). I certificati qualificati per
        # firma elettronica hanno sempre l'estensione corretta, quindi non
        # introduciamo un rischio reale in produzione.
        signer_key_usage=set(),
    )
    signers.sign_pdf(
        writer,
        meta,
        signer=signer,
        output=out_buffer,
    )
    return out_buffer.getvalue()


_URL_LOCALE_RE = re.compile(r'''(?P<attr>\b(?:src|href)=["'])(?P<url>/[^"'?#]+)''')


def _file_locale(url: str) -> Path | None:
    """Il file su disco servito all'URL ``/static/…`` o ``/media/…``."""
    from django.conf import settings
    from django.contrib.staticfiles import finders
    from django.core.exceptions import SuspiciousFileOperation

    url = unquote(url)
    static_url = '/' + settings.STATIC_URL.lstrip('/')
    media_url = '/' + settings.MEDIA_URL.lstrip('/')
    if url.startswith(static_url):
        nome = url[len(static_url):]
        try:
            trovato = finders.find(nome)   # file sorgente (sviluppo)
        except SuspiciousFileOperation:
            return None
        if trovato:
            return Path(trovato)
        radice = settings.STATIC_ROOT      # nomi con hash, dopo collectstatic
    elif url.startswith(media_url):
        nome = url[len(media_url):]
        from gestionale.media_views import file_appartiene_azienda
        from tenancy.context import current_azienda_id
        if not file_appartiene_azienda(nome, current_azienda_id()):
            return None
        radice = settings.MEDIA_ROOT
    else:
        return None
    if not radice:
        return None
    radice = Path(radice).resolve()
    percorso = (radice / nome).resolve()
    return percorso if percorso.is_file() and percorso.is_relative_to(radice) else None


def _url_locali_a_file(html: str) -> str:
    """Sostituisce gli URL ``/static/…`` e ``/media/…`` con ``file://``: così
    WeasyPrint legge CSS e logo dal disco invece di richiederli via HTTP al
    sito stesso (lento, e occupa un worker per ogni risorsa)."""
    def _sostituisci(m):
        percorso = _file_locale(m.group('url'))
        return m.group('attr') + percorso.as_uri() if percorso else m.group(0)
    return _URL_LOCALE_RE.sub(_sostituisci, html)


def render_pdf_bytes(template_path: str, context: dict, request=None) -> bytes:
    """Rende un template HTML come bytes PDF con WeasyPrint.

    CSS e immagini sotto ``/static/`` e ``/media/`` si leggono dal disco
    (``_url_locali_a_file``). ``request`` è opzionale: se passato,
    ``base_url`` punta alla root del sito per gli altri URL relativi; se
    omesso (es. invio email da job offline) il PDF esce comunque completo.
    """
    from weasyprint import HTML

    html = _url_locali_a_file(render_to_string(template_path, context, request=request))
    base_url = (
        request.build_absolute_uri('/') if request is not None else None
    )
    return HTML(string=html, base_url=base_url).write_pdf()


def crea_pdf(template_path: str, context: dict, filename: str,
             request=None) -> HttpResponse:
    """Rende un template HTML come PDF con WeasyPrint.

    Ritorna una HttpResponse con Content-Disposition inline (apertura nel
    browser). Per forzare il download basta sostituire ``inline`` con
    ``attachment`` nella header.
    """
    pdf_bytes = render_pdf_bytes(template_path, context, request=request)
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response
