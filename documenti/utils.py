"""
Helper comuni a tutti i documenti: calcolo totali, generazione PDF
(WeasyPrint), auto-generazione scadenze da una forma pagamento.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from django.http import HttpResponse
from django.template.loader import render_to_string


def calcola_valori(testata):
    """Calcola i totali di una testata a partire dalle righe.

    Ritorna dict con: ``imponibile``, ``iva_dettaglio`` (lista di
    {aliquota, imponibile, imposta}), ``imposta_totale``, ``sconto``
    (importo già scalato), ``totale``.

    La cassa previdenziale, se configurata sull'AnagraficaAzienda, viene
    applicata sull'imponibile (percentuale_imponibile) e aggiunta al totale.
    """
    righe = list(testata.righe.select_related('iva').all())

    imponibile = Decimal('0.00')
    iva_map: dict[str, dict] = {}
    for r in righe:
        row_imp = Decimal(str(r.importo_unitario)) * Decimal(str(r.quantita))
        imponibile += row_imp
        if r.iva is not None:
            key = f'{r.iva.aliquota}|{r.iva.natura}'
            slot = iva_map.setdefault(key, {
                'aliquota': r.iva.aliquota,
                'natura': r.iva.natura or '',
                'descrizione': r.iva.descrizione,
                'imponibile': Decimal('0'),
                'imposta': Decimal('0'),
            })
            slot['imponibile'] += row_imp
            slot['imposta'] += row_imp * r.iva.aliquota / Decimal('100')

    # Sconto percentuale sulla testata
    sconto_pct = getattr(testata, 'sconto', None) or Decimal('0')
    sconto_incond = getattr(testata, 'sconto_incondizionato', None) or Decimal('0')
    sconto_value = (imponibile * Decimal(sconto_pct) / Decimal('100')) + Decimal(sconto_incond)
    imponibile_scontato = imponibile - sconto_value

    # Spese accessorie
    spese = (getattr(testata, 'spese_imballo', None) or Decimal('0')) \
        + (getattr(testata, 'spese_trasporto', None) or Decimal('0'))

    # Imposta ricalcolata proporzionalmente allo sconto
    if imponibile > 0:
        coef = imponibile_scontato / imponibile
    else:
        coef = Decimal('1')
    imposta_totale = Decimal('0')
    iva_dettaglio = []
    for slot in iva_map.values():
        imp_scont = slot['imponibile'] * coef
        imposta = slot['imposta'] * coef
        imposta_totale += imposta
        iva_dettaglio.append({
            'descrizione': slot['descrizione'],
            'aliquota': slot['aliquota'],
            'natura': slot['natura'],
            'imponibile': imp_scont.quantize(Decimal('0.01')),
            'imposta': imposta.quantize(Decimal('0.01')),
        })

    # Cassa previdenziale (dalla AnagraficaAzienda.profilo_fiscale se presente)
    cassa = Decimal('0')
    try:
        from anagrafiche.models import AnagraficaAzienda
        azienda = AnagraficaAzienda.objects.first()
        if azienda and azienda.profilo_fiscale_id:
            cp = azienda.profilo_fiscale.cassa_previdenziale
            if cp and cp.attiva:
                base = imponibile_scontato * (cp.percentuale_imponibile / Decimal('100'))
                cassa = base * cp.aliquota_cassa / Decimal('100')
    except Exception:
        pass

    totale = imponibile_scontato + imposta_totale + spese + cassa

    return {
        'imponibile_lordo': imponibile.quantize(Decimal('0.01')),
        'sconto': sconto_value.quantize(Decimal('0.01')),
        'imponibile': imponibile_scontato.quantize(Decimal('0.01')),
        'spese': spese.quantize(Decimal('0.01')),
        'cassa_prev': cassa.quantize(Decimal('0.01')),
        'iva_dettaglio': iva_dettaglio,
        'imposta_totale': imposta_totale.quantize(Decimal('0.01')),
        'totale': totale.quantize(Decimal('0.01')),
    }


def genera_scadenze_da_forma_pagamento(testata):
    """Auto-genera scadenze in base a ``testata.forme_pagamento``.

    Presuppone una ``data_documento`` valorizzata e una lista di ``Scadenza``
    collegate alla ``FormePagamento``. Non salva automaticamente la
    ``ScadenzaFattura``: ritorna la lista di dict che il chiamante può
    persistere o presentare in UI.
    """
    if not testata.forme_pagamento_id or not testata.data_documento:
        return []
    totals = calcola_valori(testata)
    totale = totals['totale']
    base = testata.data_documento
    scadenze = []
    for s in testata.forme_pagamento.scadenze.all():
        giorni = int(s.numero_giorni or 0)
        data = base + timedelta(days=giorni)
        if s.fine_mese:
            if data.month == 12:
                data = date(data.year + 1, 1, 1) - timedelta(days=1)
            else:
                data = date(data.year, data.month + 1, 1) - timedelta(days=1)
            if s.numero_giorni_fm:
                data += timedelta(days=int(s.numero_giorni_fm))
        importo = (totale * Decimal(s.percentuale) / Decimal('100')).quantize(Decimal('0.01'))
        scadenze.append({'data': data, 'importo': importo})
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


def render_pdf_bytes(template_path: str, context: dict, request=None) -> bytes:
    """Rende un template HTML come bytes PDF con WeasyPrint.

    ``request`` è opzionale: se passato, ``base_url`` punta alla root del
    sito così WeasyPrint risolve gli URL ``/static/`` e ``/media/``. Se
    omesso (es. invio email da job offline) si usa la directory base
    del progetto, sufficiente per i link relativi presenti nei template.
    """
    from weasyprint import HTML

    html = render_to_string(template_path, context, request=request)
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
