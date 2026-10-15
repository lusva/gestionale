"""Filtri condivisi per cercare parole anche in campi diversi."""
from django.db.models import Q


MAX_QUERY_LENGTH = 100
CAMPI_CLIENTE = (
    'ragione_sociale', 'partita_iva', 'codice_fiscale', 'pec', 'citta',
    'contatti__nome', 'contatti__cognome', 'contatti__email', 'contatti__telefono',
)
CAMPI_CONTATTO = ('nome', 'cognome', 'email', 'telefono', 'cliente__ragione_sociale')


def normalizza_query(value):
    return ' '.join((value or '').split())[:MAX_QUERY_LENGTH]


def filtro_parole(query, *fields):
    """Ogni parola deve comparire in almeno un campo del record."""
    result = Q()
    for word in normalizza_query(query).split():
        matches = Q()
        for field in fields:
            matches |= Q(**{f'{field}__icontains': word})
        result &= matches
    return result


def filtra_clienti(queryset, params):
    """Lista ed esportazione devono contenere gli stessi clienti."""
    q = normalizza_query(params.get('q'))
    if q:
        queryset = queryset.filter(filtro_parole(q, *CAMPI_CLIENTE)).distinct()
    stati = [s for s in params.getlist('stato') if s]
    if stati:
        queryset = queryset.filter(stato__in=stati)
    settori = [s for s in params.getlist('settore') if s]
    if settori:
        queryset = queryset.filter(settore__slug__in=settori)
    return queryset.order_by('ragione_sociale', 'pk')
