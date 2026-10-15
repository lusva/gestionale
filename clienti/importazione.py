"""Anteprima ripetibile dell'import clienti e applicazione del piano approvato.

``prepara_import`` legge soltanto: non salva clienti, settori o audit. Per
confermare, ricalcolarla dentro ``transaction.atomic`` con ``lock=True`` e
confrontare il fingerprint con quello dell'anteprima conservata in sessione.
Passare a ``esegui_piano`` esclusivamente il piano appena ricalcolato lato server.
"""
from collections import Counter, defaultdict
import hashlib
import json
import re

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import F, Q, Value
from django.db.models.functions import Lower, Replace, Trim, Upper
from django.utils.text import slugify

from .forms import SDI_RE, normalizza_partita_iva, valida_codice_fiscale
from .models import Cliente, Settore


FIELD_LABELS = {
    'ragione_sociale': 'Ragione sociale',
    'partita_iva': 'Partita IVA',
    'tipo': 'Tipo',
    'settore': 'Settore',
    'codice_fiscale': 'Codice fiscale',
    'codice_sdi': 'Codice SDI',
    'pec': 'PEC',
    'indirizzo': 'Indirizzo',
    'cap': 'CAP',
    'citta': 'Città',
    'provincia': 'Provincia',
    'nazione': 'Nazione',
    'stato': 'Stato',
    'note': 'Note',
}
_ACTIONS = ('create', 'update', 'unchanged', 'error')
_UPPER_FIELDS = {'nazione', 'provincia', 'codice_fiscale', 'codice_sdi'}
_COUNTRY_RE = re.compile(r'^[A-Z]{2}$')


def _clean_vat_text(value):
    return value.strip().upper().replace(' ', '').replace('.', '').replace('-', '')


def _canonical_key(value):
    value = _clean_vat_text(value or '')
    return f'IT{value}' if len(value) == 11 and value.isascii() and value.isdigit() else value


def _normalised_vat_expression():
    value = Trim(F('partita_iva'))
    for separator in (' ', '.', '-'):
        value = Replace(value, Value(separator), Value(''))
    return Upper(value)


def _mapping_indices(headers, mapping):
    """Rifiuta mapping ambigui anche se il chiamante non li ha verificati."""
    if any(not isinstance(header, str) for header in headers):
        return {}, 'Intestazioni CSV non valide.'
    if len(headers) != len(set(headers)):
        return {}, 'Le intestazioni CSV devono essere univoche.'
    indices = {}
    for index, header in enumerate(headers):
        target = mapping.get(header, '')
        if not target:
            continue
        if target not in FIELD_LABELS:
            return {}, f'Campo di destinazione non valido: {target}.'
        if target in indices:
            return {}, f'Il campo {FIELD_LABELS[target]} è associato a più colonne.'
        indices[target] = index
    missing = {'ragione_sociale', 'partita_iva'} - indices.keys()
    if missing:
        return indices, 'Associa le colonne obbligatorie: ' + ', '.join(FIELD_LABELS[key] for key in sorted(missing)) + '.'
    return indices, ''


def _field_errors(values, nation):
    errors = []
    for field, value in values.items():
        try:
            Cliente._meta.get_field(field).clean(value, None)
        except ValidationError as exc:
            errors.append(f'{FIELD_LABELS[field]}: {"; ".join(exc.messages)}')
    if values.get('nazione') and not _COUNTRY_RE.fullmatch(values['nazione']):
        errors.append('Nazione: usa una sigla di due lettere, per esempio IT.')
    if values.get('provincia') and not _COUNTRY_RE.fullmatch(values['provincia']):
        errors.append('Provincia: usa una sigla di due lettere, per esempio MI.')
    cf = values.get('codice_fiscale')
    if cf and nation == 'IT' and not valida_codice_fiscale(cf):
        errors.append('Codice fiscale non valido: attesi 16 caratteri o 11 cifre.')
    sdi = values.get('codice_sdi')
    if sdi and not SDI_RE.fullmatch(sdi):
        errors.append('Codice SDI non valido: attesi 7 caratteri alfanumerici.')
    cap = values.get('cap')
    if cap and nation == 'IT' and not re.fullmatch(r'[0-9]{5}', cap):
        errors.append('CAP non valido: attese 5 cifre per un indirizzo italiano.')
    return errors


def _snapshot(instance):
    return {field.attname: getattr(instance, field.attname) for field in instance._meta.concrete_fields}


def _display(field, value):
    if value is None:
        return ''
    if field in ('tipo', 'stato'):
        return str(dict(Cliente._meta.get_field(field).choices).get(value, value))
    return str(value)


def _change(field, before, after):
    return {'field': field, 'label': FIELD_LABELS[field],
            'before': _display(field, before), 'after': _display(field, after)}


def prepara_import(headers, rows, mapping, *, lock=False):
    """Restituisce righe, conteggi e fingerprint JSON; nessuna scrittura.

    Celle vuote in aggiornamento mantengono il dato attuale. La partita IVA è
    una chiave di confronto: il formato storico memorizzato non viene riscritto
    quando identifica già un cliente. ``lock=True`` richiede una transazione
    esterna e blocca clienti/settori interessati fino alla conferma.
    """
    indices, mapping_error = _mapping_indices(headers, mapping)
    prepared = []
    vat_counts = Counter()
    for line, cells in enumerate(rows, start=2):
        errors = [mapping_error] if mapping_error else []
        if not isinstance(cells, (list, tuple)) or any(not isinstance(cell, str) for cell in cells):
            errors.append('Riga CSV non valida: sono attese celle di testo.')
            cells = []
        if len(cells) != len(headers):
            errors.append(f'Numero di colonne non valido: attese {len(headers)}, trovate {len(cells)}.')
        raw = {target: cells[index].strip() for target, index in indices.items() if index < len(cells)}
        name, raw_vat = raw.get('ragione_sociale', ''), raw.get('partita_iva', '')
        if not name or not raw_vat:
            errors.append('Ragione sociale e partita IVA sono obbligatorie.')
        nation = raw.get('nazione', '').upper()
        # Coerente con il form clienti: un valore arbitrario non deve diventare
        # una P.IVA estera solo perché comincia con due lettere.
        inferred_nation = nation or 'IT'
        vat, vat_error = normalizza_partita_iva(raw_vat, inferred_nation)
        if vat_error:
            errors.append('Partita IVA: ' + vat_error)
        if vat:
            vat_counts[vat] += 1
        prepared.append({
            'line': line, 'name': name, 'vat': vat or raw_vat, 'action': 'error',
            'changes': [], 'error': '', '_values': {}, '_customer_id': None,
            '_sector': None, '_raw': raw, '_errors': errors,
            '_valid_vat': vat or '', '_nation': inferred_nation,
        })

    candidates = set(vat_counts)
    candidates.update(vat[2:] for vat in vat_counts if vat.startswith('IT'))
    customers_query = Cliente.objects.annotate(_import_vat=_normalised_vat_expression()).filter(_import_vat__in=sorted(candidates)).order_by('pk')
    if lock:
        customers_query = customers_query.select_for_update()
    customers = list(customers_query)
    by_vat = defaultdict(list)
    for customer in customers:
        by_vat[_canonical_key(customer.partita_iva)].append(customer)

    sector_names = {row['_raw'].get('settore', '') for row in prepared} - {''}
    sector_slugs = {slugify(name) for name in sector_names} - {''}
    sector_ids = {customer.settore_id for customer in customers if customer.settore_id}
    sectors_query = Settore.objects.annotate(_import_name=Lower('nome')).filter(
        Q(pk__in=sector_ids) | Q(slug__in=sorted(sector_slugs))
        | Q(_import_name__in=sorted(name.lower() for name in sector_names)),
    ).order_by('pk')
    if lock:
        sectors_query = sectors_query.select_for_update()
    sectors = list(sectors_query)
    sectors_by_id = {sector.pk: sector for sector in sectors}
    sectors_by_slug = {sector.slug: sector for sector in sectors}
    sectors_by_name = defaultdict(list)
    for sector in sectors:
        sectors_by_name[sector.nome.lower()].append(sector)
    # Varianti del nome che produrrebbero lo stesso slug condividono il nome
    # della prima riga, così anche l'anteprima descrive il dato effettivo.
    proposed_sectors = {}

    for row in prepared:
        raw, errors, vat = row['_raw'], row['_errors'], row['_valid_vat']
        if vat and vat_counts[vat] > 1:
            errors.append('Partita IVA duplicata nel file: tutte le occorrenze sono escluse.')
        matches = by_vat.get(vat, [])
        if len(matches) > 1:
            errors.append('La partita IVA corrisponde a più clienti: risolvi il duplicato in anagrafica.')
        customer = matches[0] if len(matches) == 1 else None
        if customer:
            row['_customer_id'] = customer.pk
        values = {field: value for field, value in raw.items()
                  if value and field not in ('partita_iva', 'settore')}
        for field in _UPPER_FIELDS & values.keys():
            values[field] = values[field].upper()
        if 'codice_fiscale' in values:
            values['codice_fiscale'] = values['codice_fiscale'].replace(' ', '')
        for field in ('tipo', 'stato'):
            if field in values:
                values[field] = values[field].lower()
        if customer is None:
            values['partita_iva'] = vat
            for field in ('tipo', 'stato'):
                values.setdefault(field, Cliente._meta.get_field(field).get_default())
            values.setdefault('nazione', row['_nation'])
        effective_nation = values.get('nazione') or (customer.nazione if customer else row['_nation'])
        errors.extend(_field_errors(values, effective_nation))

        sector_name = raw.get('settore', '')
        sector = None
        if sector_name:
            sector_slug = slugify(sector_name)
            try:
                Settore._meta.get_field('nome').clean(sector_name, None)
                Settore._meta.get_field('slug').clean(sector_slug, None)
            except ValidationError as exc:
                errors.append('Settore: ' + '; '.join(exc.messages))
            if not sector_slug:
                errors.append('Settore: il nome deve contenere lettere o numeri.')
            sector_matches_by_id = {item.pk: item for item in sectors_by_name.get(sector_name.lower(), [])}
            if sector_slug in sectors_by_slug:
                found_by_slug = sectors_by_slug[sector_slug]
                sector_matches_by_id[found_by_slug.pk] = found_by_slug
            sector_matches = list(sector_matches_by_id.values())
            if len(sector_matches) > 1:
                errors.append('Settore ambiguo: nome e sigla corrispondono a più settori.')
            elif sector_matches:
                found = sector_matches[0]
                sector = {'id': found.pk, 'name': found.nome, 'slug': found.slug}
            elif not errors:
                sector = proposed_sectors.setdefault(sector_slug, {'id': None, 'name': sector_name, 'slug': sector_slug})

        if errors:
            row['error'] = ' '.join(dict.fromkeys(errors))
            continue
        row['_values'] = values
        row['_sector'] = sector
        for field in FIELD_LABELS:
            if field in values:
                before = getattr(customer, field) if customer else ''
                if before != values[field]:
                    row['changes'].append(_change(field, before, values[field]))
            elif field == 'settore' and sector:
                before_sector = sectors_by_id.get(customer.settore_id) if customer else None
                if before_sector is None or before_sector.pk != sector['id']:
                    row['changes'].append(_change(field, before_sector.nome if before_sector else '', sector['name']))
        row['action'] = 'create' if customer is None else ('update' if row['changes'] else 'unchanged')

    for row in prepared:
        for private in ('_raw', '_errors', '_valid_vat', '_nation'):
            del row[private]
    counts = {action: sum(row['action'] == action for row in prepared) for action in _ACTIONS}
    state = {
        'version': 1, 'headers': headers, 'mapping': mapping, 'rows': prepared,
        'customers': [_snapshot(customer) for customer in customers],
        'sectors': [_snapshot(sector) for sector in sectors],
    }
    encoded = json.dumps(state, cls=DjangoJSONEncoder, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return {'rows': prepared, 'counts': counts, 'fingerprint': hashlib.sha256(encoded).hexdigest()}


@transaction.atomic
def esegui_piano(plan):
    """Applica un piano ricalcolato e verificato lato server, tutto o niente.

    Righe invalide e invariate non causano salvataggi né audit. Eventuali
    conflitti DB annullano l'intero batch e risalgono al chiamante, che deve
    richiedere una nuova anteprima senza esporre messaggi SQL all'utente.
    """
    counts = {action: 0 for action in _ACTIONS}
    new_sectors = {}
    for row in plan['rows']:
        action = row['action']
        if action not in _ACTIONS:
            raise ValueError('Azione di importazione non valida.')
        if action in ('error', 'unchanged'):
            counts[action] += 1
            continue
        values = dict(row['_values'])
        sector = row['_sector']
        if sector:
            sector_id = sector['id']
            if sector_id is None:
                if sector['slug'] not in new_sectors:
                    new_sectors[sector['slug']] = Settore.objects.create(nome=sector['name'], slug=sector['slug']).pk
                sector_id = new_sectors[sector['slug']]
            values['settore_id'] = sector_id
        if action == 'create':
            Cliente.objects.create(**values)
        else:
            customer = Cliente.objects.select_for_update().get(pk=row['_customer_id'])
            changed_fields = {change['field'] for change in row['changes']}
            for field, value in values.items():
                if field.removesuffix('_id') in changed_fields:
                    setattr(customer, field, value)
            customer.save(update_fields=changed_fields | {'updated_at'})
        counts[action] += 1
    return counts
