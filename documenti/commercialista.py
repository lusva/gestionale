"""Pacchetto per il commercialista: uno zip con i documenti di un periodo.

- ``fatture_emesse/``: l'XML FatturaPA di ogni fattura emessa (come lo genera
  il gestionale) e, a richiesta, il PDF;
- ``fatture_ricevute/``: gli XML originali (anche firmati .p7m) delle fatture
  di acquisto importate;
- ``registro_vendite.csv`` e ``registro_acquisti.csv``: un documento per riga
  con imponibile, IVA e totale (note di credito in negativo);
- ``LEGGIMI.txt``: periodo, conteggi e i documenti senza XML.

Le fatture emesse contano per data documento; quelle ricevute per data di
registrazione (o del documento, se manca), come nella liquidazione IVA.
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.timezone import localdate

from accounts.permissions import require_perm
from audit.utils import log as audit_log

from .export_csv import _cella_sicura, _euro
from .models import TestataFattura, TestataFatturaAcquisto, TestataNotaCredito, con_incassi
from .utils import azienda_per_calcoli, calcola_valori


def periodi_rapidi(oggi: date | None = None) -> list[tuple[str, date, date]]:
    """Le scelte rapide della pagina: (etichetta, dal, al)."""
    oggi = oggi or localdate()
    primo_mese = oggi.replace(day=1)
    fine_mese_scorso = primo_mese - timedelta(days=1)
    t = (oggi.month - 1) // 3                       # trimestre in corso, da 0
    inizio_t = date(oggi.year, 3 * t + 1, 1)
    fine_t_scorso = inizio_t - timedelta(days=1)
    inizio_t_scorso = date(fine_t_scorso.year, 3 * ((fine_t_scorso.month - 1) // 3) + 1, 1)
    return [
        ('Mese scorso', fine_mese_scorso.replace(day=1), fine_mese_scorso),
        ('Mese in corso', primo_mese, oggi),
        (f'{(fine_t_scorso.month - 1) // 3 + 1}° trimestre {fine_t_scorso.year}',
         inizio_t_scorso, fine_t_scorso),
        (f'{t + 1}° trimestre {oggi.year} (in corso)', inizio_t, oggi),
        (f'Anno {oggi.year - 1}', date(oggi.year - 1, 1, 1), date(oggi.year - 1, 12, 31)),
        (f'Anno {oggi.year} (in corso)', date(oggi.year, 1, 1), oggi),
    ]


def _data(valore) -> date | None:
    try:
        return date.fromisoformat((valore or '').strip())
    except ValueError:
        return None


def _csv(righe: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';', quoting=csv.QUOTE_MINIMAL)
    for r in righe:
        w.writerow([_cella_sicura(v) for v in r])
    return ('﻿' + buf.getvalue()).encode('utf-8')


def _d(v) -> str:
    return v.strftime('%d/%m/%Y') if v else ''


def documenti_periodo(dal: date, al: date):
    fatture = (con_incassi(TestataFattura.objects.filter(data_documento__range=(dal, al)))
               .select_related('cliente').prefetch_related('righe__iva')
               .order_by('data_documento', 'sezionale', 'numero'))
    note = (TestataNotaCredito.objects.filter(data_documento__range=(dal, al))
            .select_related('cliente').prefetch_related('righe__iva')
            .order_by('data_documento', 'numero'))
    acquisti = (TestataFatturaAcquisto.objects.filter(
        Q(data_registrazione__range=(dal, al))
        | Q(data_registrazione__isnull=True, data_documento__range=(dal, al)))
        .exclude(stato=TestataFatturaAcquisto.StatoFattura.ANNULLATA)
        .select_related('fornitore').order_by('data_registrazione', 'numero'))
    return fatture, note, acquisti


def crea_zip(dal: date, al: date, *, con_pdf: bool = False, request=None) -> bytes:
    from .fatt_el import build_documento_xml, build_fattura_xml, nome_file_sdi
    from .utils import render_pdf_bytes, resolve_conto_corrente

    fatture, note, acquisti = documenti_periodo(dal, al)
    azienda = azienda_per_calcoli()
    piva = azienda.partita_iva if azienda else ''
    buf = io.BytesIO()
    senza_xml: list[str] = []
    registro_vendite = [['Documento', 'Tipo', 'Numero', 'Data', 'Cliente', 'P.IVA', 'Codice fiscale',
                         'Imponibile', 'IVA', 'Totale', 'Stato SDI', 'Pagata', 'Data pagamento',
                         'Incassato', 'File XML']]
    registro_acquisti = [['Numero interno', 'Tipo', 'Numero fornitore', 'Data documento',
                          'Data registrazione', 'Fornitore', 'P.IVA', 'Imponibile', 'IVA', 'Totale',
                          'Stato', 'Pagata', 'Data pagamento', 'File XML']]

    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in fatture:
            righe = list(f.righe.all())
            valori = calcola_valori(f, righe, azienda=azienda)
            numero = f'{f.sezionale}/{f.numero}/{f.anno}' if f.sezionale else f'{f.numero}/{f.anno}'
            nome_xml = ''
            try:
                xml = build_fattura_xml(f)
                nome_xml = nome_file_sdi(f, piva)
                zf.writestr(f'fatture_emesse/{nome_xml}', xml)
            except ValueError as exc:
                senza_xml.append(f'Fattura {numero}: {exc}')
            if con_pdf:
                ctx = {'fattura': f, 'azienda': azienda, 'righe': righe, 'scadenze': f.scadenze.all(),
                       'totals': valori, 'conto_corrente': resolve_conto_corrente(f, azienda)}
                pdf = render_pdf_bytes('documenti/pdf/fattura.html', ctx, request=request)
                zf.writestr(f'fatture_emesse/pdf/fattura_{numero.replace("/", "_")}.pdf', pdf)
            c = f.cliente
            registro_vendite.append([
                'Fattura', f.tipo_documento, numero, _d(f.data_documento),
                c.ragione_sociale if c else '', c.partita_iva if c else '', c.codice_fiscale if c else '',
                _euro(valori['imponibile'] + valori['spese'] + valori['cassa_prev'] + valori['bollo']),
                _euro(valori['imposta_totale']), _euro(valori['totale']),
                f.get_sdi_stato_display(),
                'Sì' if f.pagata else ('In parte' if f.incassato_tot else 'No'), _d(f.data_pagamento),
                _euro(valori['totale'] if f.pagata else f.incassato_tot), nome_xml,
            ])
        for nc in note:
            valori = calcola_valori(nc, list(nc.righe.all()), azienda=azienda)
            numero = f'{nc.sezionale}/{nc.numero}/{nc.anno}' if nc.sezionale else f'{nc.numero}/{nc.anno}'
            c = nc.cliente
            nome_xml = ''
            try:
                xml = build_documento_xml(nc)
                nome_xml = nome_file_sdi(nc, piva)
                zf.writestr(f'fatture_emesse/{nome_xml}', xml)
            except ValueError as exc:
                senza_xml.append(f'Nota di credito {numero}: {exc}')
            registro_vendite.append([
                'Nota di credito', nc.tipo_documento, numero, _d(nc.data_documento),
                c.ragione_sociale if c else '', c.partita_iva if c else '', c.codice_fiscale if c else '',
                _euro(-(valori['imponibile'] + valori['spese'] + valori['cassa_prev'] + valori['bollo'])),
                _euro(-valori['imposta_totale']), _euro(-valori['totale']),
                nc.get_sdi_stato_display(), '', '', '', nome_xml,
            ])
        nomi_usati: set[str] = set()
        for a in acquisti:
            nome_xml = ''
            if a.xml_originale:
                try:
                    with a.xml_originale.open('rb') as fh:
                        contenuto = fh.read()
                    nome_xml = Path(a.xml_originale.name).name
                    if nome_xml in nomi_usati:
                        nome_xml = f'{a.pk}_{nome_xml}'
                    nomi_usati.add(nome_xml)
                    zf.writestr(f'fatture_ricevute/{nome_xml}', contenuto)
                except OSError:
                    nome_xml = ''
                    senza_xml.append(f'Fattura di acquisto {a.numero}/{a.anno}: file XML non trovato')
            else:
                senza_xml.append(f'Fattura di acquisto {a.numero}/{a.anno} ({a.fornitore or ""}): '
                                 f'inserita senza XML')
            nc = a.tipo_documento == 'TD04'
            segno = Decimal('-1') if nc else Decimal('1')
            fo = a.fornitore
            registro_acquisti.append([
                f'{a.numero}/{a.anno}', a.tipo_documento, a.numero_fornitore or '',
                _d(a.data_fornitore or a.data_documento), _d(a.data_registrazione),
                str(fo) if fo else '', fo.partita_iva if fo else '',
                _euro(segno * (a.imponibile or 0)), _euro(segno * (a.imposta or 0)),
                _euro(segno * (a.totale_documento or 0)),
                a.get_stato_display(), 'Sì' if a.pagata else 'No', _d(a.data_pagamento), nome_xml,
            ])
        zf.writestr('registro_vendite.csv', _csv(registro_vendite))
        zf.writestr('registro_acquisti.csv', _csv(registro_acquisti))
        leggimi = [
            f'Documenti dal {_d(dal)} al {_d(al)}' + (f' — {azienda.ragione_sociale}' if azienda else ''),
            f'Generato il {localdate():%d/%m/%Y} dal gestionale.',
            '',
            f'Fatture emesse: {len(registro_vendite) - 1 - len(note)} · note di credito: {len(note)}'
            f' · fatture ricevute: {len(registro_acquisti) - 1}',
            '',
            'fatture_emesse/   XML FatturaPA generati dal gestionale' + (' e PDF' if con_pdf else ''),
            'fatture_ricevute/ XML originali ricevuti dal SDI (anche firmati .p7m)',
            'registro_vendite.csv / registro_acquisti.csv: separatore «;», decimali con la virgola;',
            '  note di credito con importi negativi.',
            '',
            'Se le fatture sono state trasmesse da un altro canale (es. il portale del',
            'provider), gli XML ufficiali sono quelli conservati da quel canale.',
        ]
        if senza_xml:
            leggimi += ['', 'Documenti senza XML nel pacchetto:'] + [f'- {x}' for x in senza_xml]
        zf.writestr('LEGGIMI.txt', '\n'.join(leggimi) + '\n')
    return buf.getvalue()


@login_required
@require_perm('documenti.vedi')
def export_commercialista(request):
    rapidi = periodi_rapidi()
    dal = _data(request.GET.get('dal')) or rapidi[2][1]
    al = _data(request.GET.get('al')) or rapidi[2][2]
    if al < dal:
        dal, al = al, dal
    if request.GET.get('scarica'):
        contenuto = crea_zip(dal, al, con_pdf=bool(request.GET.get('pdf')), request=request)
        audit_log('export', request=request, target_type='documenti',
                  target_label=f'Pacchetto commercialista {dal:%d/%m/%Y} – {al:%d/%m/%Y}',
                  meta={'dal': dal.isoformat(), 'al': al.isoformat()})
        response = HttpResponse(contenuto, content_type='application/zip')
        response['Content-Disposition'] = (
            f'attachment; filename="documenti_{dal:%Y%m%d}_{al:%Y%m%d}.zip"')
        return response
    fatture, note, acquisti = documenti_periodo(dal, al)
    return render(request, 'documenti/export_commercialista.html', {
        'active_nav': 'fatture',
        'dal': dal, 'al': al, 'rapidi': rapidi,
        'n_fatture': fatture.count(), 'n_note': note.count(), 'n_acquisti': acquisti.count(),
        'n_acquisti_senza_xml': acquisti.filter(Q(xml_originale='') | Q(xml_originale__isnull=True)).count(),
    })
