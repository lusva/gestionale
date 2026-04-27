"""
Export CSV per fatture, scadenziari e costi.

Tutti gli export usano BOM UTF-8 (compatibilità Excel) e separatore ``;``
(default italiano). Il filtro è coerente con la list view: chi visita
``/documenti/fatture/?anno=2026&pagata=no`` può scaricare lo stesso slice
da ``/documenti/fatture/export/?anno=2026&pagata=no``.
"""
from __future__ import annotations

import csv
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.utils.timezone import now

from accounts.permissions import require_perm

from .models import (
    RigaFatturaAcquisto,
    ScadenzaFattura,
    ScadenzaFatturaAcquisto,
    TestataFattura,
    TestataFatturaAcquisto,
)


def _csv_response(filename: str) -> tuple[HttpResponse, csv.writer]:
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write('﻿')  # BOM UTF-8 per Excel
    writer = csv.writer(response, delimiter=';', quoting=csv.QUOTE_MINIMAL)
    return response, writer


def _euro(v):
    if v is None:
        return ''
    # Decimali con virgola per locale italiana
    return f'{Decimal(v):.2f}'.replace('.', ',')


@login_required
@require_perm('documenti.vedi')
def fatture_export(request):
    qs = TestataFattura.objects.select_related('cliente').order_by(
        '-anno', '-numero',
    )
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(
            Q(numero__icontains=q)
            | Q(cliente__ragione_sociale__icontains=q)
            | Q(cliente__partita_iva__icontains=q),
        )
    anno = (request.GET.get('anno') or '').strip()
    if anno.isdigit():
        qs = qs.filter(anno=int(anno))
    pagata = request.GET.get('pagata')
    if pagata == 'si':
        qs = qs.filter(pagata=True)
    elif pagata == 'no':
        qs = qs.filter(pagata=False)
    tipo = (request.GET.get('tipo') or '').strip()
    if tipo:
        qs = qs.filter(tipo_documento=tipo)

    response, w = _csv_response(f'fatture_{now().date().isoformat()}.csv')
    w.writerow([
        'Numero', 'Anno', 'Data', 'Tipo', 'Cliente', 'P.IVA', 'CF',
        'Imponibile', 'Pagata', 'Data pagamento', 'Note',
    ])
    for f in qs:
        cliente = f.cliente
        w.writerow([
            f.numero, f.anno,
            f.data_documento.strftime('%d/%m/%Y') if f.data_documento else '',
            f.tipo_documento,
            cliente.ragione_sociale if cliente else '',
            cliente.partita_iva if cliente else '',
            cliente.codice_fiscale if cliente else '',
            _euro(f.imponibile),
            'Sì' if f.pagata else 'No',
            f.data_pagamento.strftime('%d/%m/%Y') if f.data_pagamento else '',
            (f.note or '').replace('\n', ' ').strip(),
        ])
    return response


@login_required
@require_perm('documenti.vedi')
def scadenziario_attivo_export(request):
    today = now().date()
    qs = ScadenzaFattura.objects.select_related('fattura__cliente').filter(
        fattura__pagata=False,
    ).order_by('data')
    cliente_id = request.GET.get('cliente')
    if cliente_id and cliente_id.isdigit():
        qs = qs.filter(fattura__cliente_id=int(cliente_id))

    response, w = _csv_response(f'scadenziario_attivo_{today.isoformat()}.csv')
    w.writerow([
        'Cliente', 'P.IVA', 'Fattura', 'Anno', 'Data scadenza',
        'Importo', 'Giorni', 'Stato',
    ])
    for s in qs:
        f = s.fattura
        cli = f.cliente
        giorni = (s.data - today).days
        bucket = 'Scaduta' if giorni < 0 else ('In scadenza' if giorni <= 30 else 'Futura')
        w.writerow([
            cli.ragione_sociale if cli else '',
            cli.partita_iva if cli else '',
            f.numero, f.anno,
            s.data.strftime('%d/%m/%Y'),
            _euro(s.importo),
            giorni, bucket,
        ])
    return response


@login_required
@require_perm('documenti.vedi')
def scadenziario_passivo_export(request):
    today = now().date()
    qs = ScadenzaFatturaAcquisto.objects.select_related('fattura__fornitore').order_by(
        'data_scadenza',
    )
    fornitore_id = request.GET.get('fornitore')
    if fornitore_id and fornitore_id.isdigit():
        qs = qs.filter(fattura__fornitore_id=int(fornitore_id))

    response, w = _csv_response(f'scadenziario_passivo_{today.isoformat()}.csv')
    w.writerow([
        'Fornitore', 'P.IVA', 'Fattura', 'Anno', 'Numero rif. fornitore',
        'Data scadenza', 'Modalità', 'IBAN',
        'Importo', 'Pagato', 'Residuo', 'Giorni', 'Stato',
    ])
    for s in qs:
        residuo = s.importo_residuo
        if residuo <= 0:
            continue
        f = s.fattura
        forn = f.fornitore
        giorni = (s.data_scadenza - today).days
        bucket = 'Scaduta' if giorni < 0 else ('In scadenza' if giorni <= 30 else 'Futura')
        w.writerow([
            forn.ragione_sociale if forn else '',
            forn.partita_iva if forn else '',
            f.numero, f.anno, f.numero_fornitore or '',
            s.data_scadenza.strftime('%d/%m/%Y'),
            s.modalita_pagamento or '', s.iban or '',
            _euro(s.importo),
            _euro(s.importo_pagato),
            _euro(residuo),
            giorni, bucket,
        ])
    return response


@login_required
@require_perm('documenti.vedi')
def costi_per_categoria_export(request):
    qs = RigaFatturaAcquisto.objects.select_related(
        'categoria_costo', 'testata__fornitore',
    ).filter(testata__stato=TestataFatturaAcquisto.StatoFattura.CONFERMATA)
    anno = request.GET.get('anno')
    if anno and anno.isdigit():
        qs = qs.filter(testata__anno=int(anno))
    fornitore_id = request.GET.get('fornitore')
    if fornitore_id and fornitore_id.isdigit():
        qs = qs.filter(testata__fornitore_id=int(fornitore_id))

    response, w = _csv_response(f'costi_categoria_{now().date().isoformat()}.csv')
    w.writerow([
        'Categoria', 'Codice cat.', 'Fornitore', 'Fattura', 'Data',
        'Descrizione', 'Quantità', 'Prezzo unit.', 'Imponibile',
    ])
    for r in qs:
        cat = r.categoria_costo
        forn = r.testata.fornitore
        w.writerow([
            cat.descrizione if cat else 'Non categorizzato',
            cat.codice if cat else '',
            forn.ragione_sociale if forn else '',
            f'{r.testata.numero}/{r.testata.anno}',
            r.testata.data_documento.strftime('%d/%m/%Y') if r.testata.data_documento else '',
            (r.descrizione or '').replace('\n', ' ').strip(),
            r.quantita,
            _euro(r.prezzo_unitario),
            _euro(r.imponibile),
        ])
    return response
