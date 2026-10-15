"""Serializer plain-python per i modelli esposti via API.

Nessun framework (niente DRF): solo funzioni che producono dict
JSON-serializzabili. Se l'API cresce, sostituire con DRF.
"""
from __future__ import annotations

from decimal import Decimal


def cliente_to_dict(c):
    return {
        'id': c.pk,
        'ragione_sociale': c.ragione_sociale,
        'tipo': c.tipo,
        'settore': c.settore.nome if c.settore_id else None,
        'partita_iva': c.partita_iva,
        'codice_fiscale': c.codice_fiscale,
        'codice_sdi': c.codice_sdi,
        'pec': c.pec,
        'indirizzo': c.indirizzo,
        'cap': c.cap,
        'citta': c.citta,
        'provincia': c.provincia,
        'nazione': c.nazione,
        'stato': c.stato,
        'created_at': c.created_at.isoformat() if c.created_at else None,
        'updated_at': c.updated_at.isoformat() if c.updated_at else None,
    }


def opportunita_to_dict(o):
    return {
        'id': o.pk,
        'titolo': o.titolo,
        'cliente': {'id': o.cliente_id, 'ragione_sociale': o.cliente.ragione_sociale},
        'valore': float(o.valore),
        'stadio': o.stadio,
        'probabilita': o.probabilita,
        'chiusura_prevista': o.chiusura_prevista.isoformat() if o.chiusura_prevista else None,
        'owner': o.owner.email if o.owner_id else None,
        'created_at': o.created_at.isoformat() if o.created_at else None,
        'updated_at': o.updated_at.isoformat() if o.updated_at else None,
    }


def attivita_to_dict(a):
    return {
        'id': a.pk,
        'tipo': a.tipo,
        'titolo': a.titolo,
        'data': a.data.isoformat() if a.data else None,
        'completata': a.completata,
        'cliente_id': a.cliente_id,
        'opportunita_id': a.opportunita_id,
        'owner': a.owner.email if a.owner_id else None,
    }


# === v2 ===

def _importo(d):
    return None if d is None else str(d)


def url_assoluto(path: str) -> str:
    from django.conf import settings
    return f'{settings.SITE_BASE_URL.rstrip("/")}{path}'


def cliente_v2(c):
    """Il cliente con i predefiniti che servono a fatturargli."""
    d = cliente_to_dict(c)
    d['forma_pagamento'] = (
        {'id': c.forme_pagamento_id, 'nome': str(c.forme_pagamento)}
        if c.forme_pagamento_id else None)
    d['posizione_iva'] = posizione_iva_to_dict(c.posizione_iva) if c.posizione_iva_id else None
    return d


def posizione_iva_to_dict(p):
    return {'id': p.pk, 'descrizione': p.descrizione, 'aliquota': _importo(p.aliquota),
            'natura': p.natura or ''}


def forma_pagamento_to_dict(f):
    return {'id': f.pk, 'nome': str(f), 'modalita': f.modalita_pagamento,
            'scadenze': [{'giorni': s.numero_giorni, 'percentuale': _importo(s.percentuale),
                          'fine_mese': s.fine_mese} for s in f.scadenze.all()]}


def conto_corrente_to_dict(c):
    return {'id': c.pk, 'banca': c.banca, 'iban': c.iban}


def valori_to_dict(v):
    return {
        'imponibile': _importo(v['imponibile']),
        'sconto': _importo(v['sconto']),
        'spese': _importo(v['spese']),
        'cassa_prev': _importo(v['cassa_prev']),
        'imposta': _importo(v['imposta_totale']),
        'totale': _importo(v['totale']),
        'iva': [{'descrizione': s['descrizione'], 'aliquota': _importo(s['aliquota']),
                 'natura': s['natura'], 'imponibile': _importo(s['imponibile']),
                 'imposta': _importo(s['imposta'])} for s in v['iva_dettaglio']],
    }


def riga_to_dict(r):
    return {
        'numero_riga': r.numero_riga,
        'descrizione': r.descrizione_libera or (str(r.articolo) if r.articolo_id else ''),
        'quantita': _importo(r.quantita),
        'um': r.um or '',
        'importo_unitario': _importo(r.importo_unitario),
        'iva': posizione_iva_to_dict(r.iva) if r.iva is not None else None,
        'imponibile': _importo(r.imponibile.quantize(Decimal('0.01'))),
    }


def fattura_v2(f, *, dettaglio=False, valori=None):
    from django.urls import reverse

    from documenti.utils import calcola_valori
    if valori is None:
        valori = calcola_valori(f)
    d = {
        'id': f.pk,
        'numero': f.numero,
        'anno': f.anno,
        'sezionale': f.sezionale,
        'etichetta': f'{f.sezionale + "/" if f.sezionale else ""}{f.numero}/{f.anno}',
        'tipo_documento': f.tipo_documento,
        'data_documento': f.data_documento.isoformat() if f.data_documento else None,
        'cliente': ({'id': f.cliente_id, 'ragione_sociale': f.cliente.ragione_sociale}
                    if f.cliente_id else None),
        'imponibile': _importo(valori['imponibile']),
        'totale': _importo(valori['totale']),
        'pagata': f.pagata,
        'data_pagamento': f.data_pagamento.isoformat() if f.data_pagamento else None,
        'sdi_stato': f.sdi_stato,
        'url': url_assoluto(reverse('documenti:fattura_detail', args=[f.pk])),
    }
    if dettaglio:
        d['valori'] = valori_to_dict(valori)
        d['forma_pagamento'] = str(f.forme_pagamento) if f.forme_pagamento_id else None
        d['note'] = f.note or ''
        d['righe'] = [riga_to_dict(r) for r in f.righe.select_related('iva', 'articolo')]
        d['scadenze'] = [{'data': s.data.isoformat(), 'importo': _importo(s.importo),
                          'incassato': _importo(s.importo_incassato),
                          'residuo': _importo(s.importo_residuo)}
                         for s in f.scadenze.prefetch_related('incassi')]
    return d
