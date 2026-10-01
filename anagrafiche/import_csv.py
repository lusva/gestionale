"""
Import CSV per anagrafiche fiscali (Articolo, Fornitore).

Versione streamlined del pattern usato in ``clienti.views.import_csv``: niente
wizard a 2 step, niente preview — l'utente carica il CSV con header e i campi
vengono mappati per nome diretto. Update_or_create per ``codice`` (Articolo)
o ``partita_iva`` (Fornitore) per fare upsert.

In aggiornamento vengono impostati solo i campi la cui colonna è presente e
non vuota: un CSV parziale non azzera i dati esistenti. Ogni import gira in
una transazione; le righe non valide diventano righe di errore.
"""
from __future__ import annotations

import csv
import io
import re
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render

from clienti.forms import normalizza_partita_iva, valida_codice_fiscale

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


_VERO = {'true', '1', 'sì', 'si', 'yes', 'x', 'vero'}
_FALSO = {'false', '0', 'no', 'falso'}
_MAX_ERRORI_MOSTRATI = 10


def parse_decimale(raw: str) -> Decimal:
    """Parsa numeri in formato italiano (``1.234,56``) o inglese
    (``1,234.56`` / ``1234.56``). Solleva ``ValueError`` se non valido.

    Regole: se compaiono sia ``.`` che ``,`` il separatore decimale è quello
    più a destra; con la sola virgola è decimale (``12,5``) salvo gruppi di
    migliaia multipli; con il solo punto è decimale salvo ripetizioni
    (``1.234.567``).
    """
    v = (raw or '').strip().replace('€', '').replace(' ', '').replace('\u00a0', '')
    if not v:
        raise ValueError('valore vuoto')
    if '.' in v and ',' in v:
        if v.rfind(',') > v.rfind('.'):
            v = v.replace('.', '').replace(',', '.')
        else:
            v = v.replace(',', '')
    elif ',' in v:
        v = v.replace(',', '') if v.count(',') > 1 else v.replace(',', '.')
    elif v.count('.') > 1:
        v = v.replace('.', '')
    if not re.fullmatch(r'[+-]?\d+(\.\d+)?', v):
        raise ValueError(f'numero non valido: {raw}')
    try:
        return Decimal(v)
    except InvalidOperation as exc:
        raise ValueError(f'numero non valido: {raw}') from exc


def _riepilogo_errori(request, errori):
    if not errori:
        return
    righe = '; '.join(f'riga {n}: {msg}' for n, msg in errori[:_MAX_ERRORI_MOSTRATI])
    extra = len(errori) - _MAX_ERRORI_MOSTRATI
    if extra > 0:
        righe += f' (e altri {extra})'
    messages.warning(request, f'Righe scartate — {righe}.')


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
    errori: list[tuple[int, str]] = []
    um_validi = set(Articolo.UM.values)
    max_codice = Articolo._meta.get_field('codice').max_length

    def get(row, key):
        i = idx.get(key)
        if i is None or i >= len(row):
            return ''
        return (row[i] or '').strip()

    def scarta(lineno, msg):
        nonlocal skipped
        skipped += 1
        errori.append((lineno, msg))

    with transaction.atomic():
        for lineno, row in enumerate(rows, start=2):
            codice = get(row, 'codice')
            if not codice:
                scarta(lineno, 'codice mancante')
                continue
            if len(codice) > max_codice:
                scarta(lineno, f'codice troppo lungo (max {max_codice})')
                continue
            valori = {}
            for campo, maxlen in (('descrizione', 255), ('scelta', 30)):
                v = get(row, campo)
                if v:
                    valori[campo] = v[:maxlen]
            um = get(row, 'um').upper()
            if um:
                if um not in um_validi:
                    scarta(lineno, f'unità di misura non valida: {um}')
                    continue
                valori['um'] = um
            obsoleto = get(row, 'obsoleto').lower()
            if obsoleto:
                if obsoleto in _VERO:
                    valori['obsoleto'] = True
                elif obsoleto in _FALSO:
                    valori['obsoleto'] = False
                else:
                    scarta(lineno, f'valore obsoleto non valido: {obsoleto}')
                    continue
            prezzo_raw = get(row, 'prezzo_listino')
            if prezzo_raw:
                try:
                    prezzo = parse_decimale(prezzo_raw).quantize(Decimal('0.01'))
                except (ValueError, InvalidOperation):
                    scarta(lineno, f'prezzo non valido: {prezzo_raw}')
                    continue
                if prezzo < 0 or prezzo >= Decimal('100000000'):
                    scarta(lineno, f'prezzo fuori intervallo: {prezzo_raw}')
                    continue
                valori['prezzo_listino'] = prezzo

            esistenti = list(Articolo.objects.filter(codice=codice)[:2])
            if len(esistenti) > 1:
                scarta(lineno, f'codice {codice} duplicato in anagrafica')
                continue
            if esistenti:
                art = esistenti[0]
                for campo, v in valori.items():
                    setattr(art, campo, v)
                art.save()
                updated += 1
            else:
                Articolo.objects.create(codice=codice, **valori)
                created += 1

    messages.success(
        request,
        f'Articoli: {created} creati, {updated} aggiornati, {skipped} scartati.',
    )
    _riepilogo_errori(request, errori)
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

    I fornitori eliminati (soft delete) non vengono ripristinati: la riga
    viene scartata con un errore.
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
    errori: list[tuple[int, str]] = []

    def get(row, key):
        i = idx.get(key)
        if i is None or i >= len(row):
            return ''
        return (row[i] or '').strip()

    def scarta(lineno, msg):
        nonlocal skipped
        skipped += 1
        errori.append((lineno, msg))

    with transaction.atomic():
        for lineno, row in enumerate(rows, start=2):
            rs = get(row, 'ragione_sociale')
            piva_raw = get(row, 'partita_iva')
            if not rs and not piva_raw:
                scarta(lineno, 'ragione_sociale e partita_iva mancanti')
                continue
            piva, errore = normalizza_partita_iva(piva_raw)
            if errore:
                scarta(lineno, f'P.IVA non valida: {piva_raw}')
                continue
            valori = {}
            if rs:
                valori['ragione_sociale'] = rs[:255]
            for campo, maxlen in (('nome', 100), ('cognome', 100),
                                  ('email', 254), ('telefono', 20)):
                v = get(row, campo)
                if v:
                    valori[campo] = v[:maxlen]
            cf = get(row, 'codice_fiscale').upper().replace(' ', '')
            if cf:
                if not valida_codice_fiscale(cf):
                    scarta(lineno, f'codice fiscale non valido: {cf}')
                    continue
                valori['codice_fiscale'] = cf

            if piva:
                # P.IVA storiche possono essere salvate senza prefisso IT.
                candidati = [piva, piva[2:]] if piva.startswith('IT') else [piva]
                qs = Fornitore.objects.filter(partita_iva__in=candidati)
            else:
                # Fallback: lookup per ragione_sociale
                qs = Fornitore.objects.filter(ragione_sociale=rs)
            esistenti = list(qs[:2])
            if len(esistenti) > 1:
                scarta(lineno, 'più fornitori corrispondenti (P.IVA/ragione sociale)')
                continue
            if esistenti and esistenti[0].soft_delete:
                scarta(
                    lineno,
                    f'il fornitore «{esistenti[0]}» è stato eliminato: '
                    'ripristinalo manualmente se necessario',
                )
                continue
            try:
                with transaction.atomic():
                    if esistenti:
                        forn = esistenti[0]
                        for campo, v in valori.items():
                            setattr(forn, campo, v)
                        forn.save()
                        updated += 1
                    else:
                        Fornitore.objects.create(partita_iva=piva or None, **valori)
                        created += 1
            except IntegrityError:
                scarta(lineno, 'dati duplicati (P.IVA o codice fiscale già usati)')

    messages.success(
        request,
        f'Fornitori: {created} creati, {updated} aggiornati, {skipped} scartati.',
    )
    _riepilogo_errori(request, errori)
    audit_log(
        Azione.IMPORT, target_type='Fornitore',
        target_label=f'{upload.name} ({created+updated} righe)',
        request=request,
        meta={'created': created, 'updated': updated, 'skipped': skipped},
    )
    return redirect('anagrafiche:fornitore_list')
