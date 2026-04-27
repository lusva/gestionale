"""Serializer plain-python per i modelli esposti via API.

Nessun framework (niente DRF): solo funzioni che producono dict
JSON-serializzabili. Se l'API cresce, sostituire con DRF.
"""
from __future__ import annotations


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
