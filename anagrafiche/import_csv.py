"""
Import CSV per anagrafiche fiscali (Articolo, Fornitore).

Versione streamlined del pattern usato in ``clienti.views.import_csv``: niente
wizard a 2 step, niente preview — l'utente carica il CSV con header e i campi
vengono mappati per nome diretto. Update_or_create per ``codice`` (Articolo)
o ``partita_iva`` (Fornitore) per fare upsert.
"""
from __future__ import annotations

import csv
import io

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render

from accounts.permissions import require_perm
from audit.models import Azione
from audit.utils import log as audit_log

from .models import Articolo, Fornitore


def _csv_decode_and_parse(raw_bytes: bytes):
    try:
        decoded = raw_bytes.decode('utf-8-sig')
    except UnicodeDecodeError:
        decoded = raw_bytes.decode('latin-1')
    sample = decoded[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(decoded), dialect=dialect)
    rows = list(reader)
    if not rows:
        return [], []
    headers = [h.strip().lower() for h in rows[0]]
    return headers, rows[1:]


@login_required
@require_perm('clienti.importa')
def articoli_import(request):
    """Import CSV per articoli. Header attesi (in qualunque ordine):
    ``codice`` (obbligatorio), ``descrizione``, ``um``, ``prezzo_listino``,
    ``scelta``, ``obsoleto``.
    """
    if request.method != 'POST':
        return render(request, 'anagrafiche/import_articoli.html', {
            'active_nav': 'articoli',
        })

    upload = request.FILES.get('file')
    if not upload:
        messages.error(request, 'Seleziona un file CSV.')
        return redirect('anagrafiche:articoli_import')
    headers, rows = _csv_decode_and_parse(upload.read())
    if 'codice' not in headers:
        messages.error(request, "Header 'codice' obbligatorio.")
        return redirect('anagrafiche:articoli_import')

    idx = {h: i for i, h in enumerate(headers)}
    created = updated = skipped = 0

    def get(row, key):
        i = idx.get(key)
        if i is None or i >= len(row):
            return ''
        return (row[i] or '').strip()

    for row in rows:
        codice = get(row, 'codice')
        if not codice:
            skipped += 1
            continue
        defaults = {
            'descrizione': get(row, 'descrizione'),
            'scelta': get(row, 'scelta'),
            'um': get(row, 'um') or 'PZ',
            'obsoleto': get(row, 'obsoleto').lower() in {'true', '1', 'sì', 'si', 'yes'},
        }
        prezzo = get(row, 'prezzo_listino') or '0'
        try:
            from decimal import Decimal
            defaults['prezzo_listino'] = Decimal(prezzo.replace(',', '.'))
        except Exception:
            defaults['prezzo_listino'] = 0
        _, was_created = Articolo.objects.update_or_create(
            codice=codice, defaults=defaults,
        )
        if was_created:
            created += 1
        else:
            updated += 1

    messages.success(
        request,
        f'Articoli: {created} creati, {updated} aggiornati, {skipped} scartati.',
    )
    audit_log(
        Azione.IMPORT, target_type='Articolo',
        target_label=f'{upload.name} ({created+updated} righe)',
        request=request,
        meta={'created': created, 'updated': updated, 'skipped': skipped},
    )
    return redirect('anagrafiche:articolo_list')


@login_required
@require_perm('clienti.importa')
def fornitori_import(request):
    """Import CSV per fornitori. Header attesi:
    ``ragione_sociale`` (obbligatorio), ``partita_iva``, ``codice_fiscale``,
    ``email``, ``telefono``, ``nome``, ``cognome``.
    """
    if request.method != 'POST':
        return render(request, 'anagrafiche/import_fornitori.html', {
            'active_nav': 'fornitori',
        })

    upload = request.FILES.get('file')
    if not upload:
        messages.error(request, 'Seleziona un file CSV.')
        return redirect('anagrafiche:fornitori_import')
    headers, rows = _csv_decode_and_parse(upload.read())
    if 'ragione_sociale' not in headers and 'partita_iva' not in headers:
        messages.error(
            request, "Almeno uno tra 'ragione_sociale' e 'partita_iva' è obbligatorio."
        )
        return redirect('anagrafiche:fornitori_import')

    idx = {h: i for i, h in enumerate(headers)}
    created = updated = skipped = 0

    def get(row, key):
        i = idx.get(key)
        if i is None or i >= len(row):
            return ''
        return (row[i] or '').strip()

    for row in rows:
        rs = get(row, 'ragione_sociale')
        piva_raw = get(row, 'partita_iva').upper().replace(' ', '')
        if not rs and not piva_raw:
            skipped += 1
            continue
        piva = piva_raw[2:] if piva_raw.startswith('IT') else piva_raw
        if piva and (not piva.isdigit() or len(piva) != 11):
            skipped += 1
            continue
        lookup = {}
        if piva:
            lookup['partita_iva'] = f'IT{piva}'
        else:
            # Fallback: lookup per ragione_sociale (deve essere unique manuale)
            lookup['ragione_sociale'] = rs
        defaults = {
            'ragione_sociale': rs or None,
            'nome': get(row, 'nome') or None,
            'cognome': get(row, 'cognome') or None,
            'codice_fiscale': get(row, 'codice_fiscale') or None,
            'email': get(row, 'email') or None,
            'telefono': get(row, 'telefono') or None,
            'soft_delete': False,
        }
        try:
            _, was_created = Fornitore.objects.update_or_create(
                **lookup, defaults=defaults,
            )
            if was_created:
                created += 1
            else:
                updated += 1
        except Exception:
            skipped += 1

    messages.success(
        request,
        f'Fornitori: {created} creati, {updated} aggiornati, {skipped} scartati.',
    )
    audit_log(
        Azione.IMPORT, target_type='Fornitore',
        target_label=f'{upload.name} ({created+updated} righe)',
        request=request,
        meta={'created': created, 'updated': updated, 'skipped': skipped},
    )
    return redirect('anagrafiche:fornitore_list')
