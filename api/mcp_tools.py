"""I tool del server MCP del gestionale: che cosa sa fare chi si collega.

Ogni tool ha un nome, un titolo, una descrizione per il modello, lo schema
JSON dei suoi argomenti, il permesso del ruolo che serve e se legge o scrive.
I tool di lettura rispondono con una tabella (``colonne`` + ``elementi``) o
con un documento; quelli di scrittura con ciò che hanno creato o cambiato.

La logica dei documenti sta in ``documenti/servizi.py``, la stessa delle
viste e dell'API v2: qui si traducono argomenti e risultati. Clienti,
documenti, fornitori e opportunità si indicano come li direbbe una persona
(«Rossi», «fattura 12/2026»): un nome ambiguo è un errore che elenca le
possibilità, mai una scelta a caso.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Callable

from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import localdate, now

from documenti import servizi
from documenti.servizi import DatiNonValidi

from .serializers import cliente_v2, url_assoluto


@dataclass
class Tool:
    nome: str
    titolo: str
    descrizione: str
    area: str
    proprieta: dict
    obbligatori: tuple
    esegui: Callable
    perm: str
    lettura: bool = True
    esempi: list = field(default_factory=list)

    def schema(self) -> dict:
        prop = dict(self.proprieta)
        if not self.lettura:
            prop['anteprima'] = {
                'type': 'boolean',
                'description': 'true = controlla i dati e mostra il risultato senza salvare nulla.'}
            prop['chiave'] = {
                'type': 'string',
                'description': 'Chiave di idempotenza: la stessa chiave ripetuta non crea un '
                               'secondo documento.'}
        return {'type': 'object', 'properties': prop, 'required': list(self.obbligatori),
                'additionalProperties': False}

    def descrittore(self) -> dict:
        return {
            'name': self.nome,
            'title': self.titolo,
            'description': self.descrizione,
            'inputSchema': self.schema(),
            'annotations': {
                'title': self.titolo,
                'readOnlyHint': self.lettura,
                'destructiveHint': False,
                'idempotentHint': self.lettura,
                'openWorldHint': False,
            },
            '_meta': {'area': self.area, 'permesso': self.perm},
        }


REGISTRO: dict[str, Tool] = {}


def tool(nome, titolo, descrizione, *, area, perm, proprieta=None, obbligatori=(),
         lettura=True):
    def registra(fn):
        REGISTRO[nome] = Tool(nome, titolo, descrizione, area, proprieta or {},
                              tuple(obbligatori), fn, perm, lettura)
        return fn
    return registra


# --------------------------------------------------------------------------- #
# Schemi e risultati comuni
# --------------------------------------------------------------------------- #

S = {'type': 'string'}
DATA = {'type': 'string', 'description': 'Data AAAA-MM-GG.'}
CLIENTE = {'type': 'string', 'description': 'Il cliente: ragione sociale (anche parziale), '
                                            'partita IVA o id.'}
NUMERO = {'type': 'integer', 'description': 'Numero del documento.'}
ANNO = {'type': 'integer', 'description': "Anno del documento (se manca, l'anno in corso)."}
SEZIONALE = {'type': 'string', 'description': 'Sezionale di numerazione, di solito vuoto.'}
TIPI_DOC = list(servizi.MODELLI)
RIGA = {
    'type': 'object',
    'properties': {
        'descrizione': {'type': 'string', 'description': 'Che cosa si vende o si consegna.'},
        'quantita': {'type': 'number', 'description': 'Quantità (1 se manca).'},
        'prezzo': {'type': 'number', 'description': 'Prezzo unitario in euro, IVA esclusa.'},
        'aliquota_iva': {'type': 'number', 'description': 'Aliquota IVA in % (22, 10, 4). Se manca, '
                                                          "quella predefinita del cliente."},
        'iva_id': {'type': 'integer', 'description': 'Posizione IVA per id (vedi tabelle_fatturazione), '
                                                     'in alternativa ad aliquota_iva.'},
        'articolo': {'type': 'string', 'description': "Codice dell'articolo di magazzino, se c'è."},
        'um': {'type': 'string', 'enum': ['PZ', 'MQ', 'ML', 'KG', 'LT', 'ORE', 'N']},
    },
    'required': ['descrizione'],
}
DOC_COMUNI = {
    'cliente': CLIENTE,
    'data_documento': {'type': 'string', 'description': 'Data AAAA-MM-GG (oggi se manca).'},
    'pagamento': {'type': 'string', 'description': 'Forma di pagamento per nome («bonifico 30 gg»); '
                                                   'se manca, quella del cliente.'},
    'note': S,
    'righe': {'type': 'array', 'items': RIGA, 'minItems': 1},
}


def _d(x):
    if x is None:
        return None
    if isinstance(x, Decimal):
        x = x.quantize(Decimal('0.01'))
    return str(x)


def _giorno(valore, campo, *, predefinito=None):
    if valore in (None, ''):
        return predefinito
    try:
        return datetime.fromisoformat(str(valore)[:10]).date()
    except ValueError:
        raise DatiNonValidi({campo: 'Data non valida (AAAA-MM-GG).'}) from None


def _intero(valore, campo, *, predefinito=None, minimo=None, massimo=None):
    """Un intero dagli argomenti del tool: non numerico → errore sul campo."""
    if valore in (None, ''):
        return predefinito
    if isinstance(valore, bool):
        raise DatiNonValidi({campo: 'Deve essere un numero intero.'})
    try:
        n = int(Decimal(str(valore).strip()))
    except (ArithmeticError, ValueError, TypeError):
        raise DatiNonValidi({campo: 'Deve essere un numero intero.'}) from None
    if minimo is not None:
        n = max(minimo, n)
    if massimo is not None:
        n = min(n, massimo)
    return n


def tabella(titolo: str, colonne: list[tuple[str, str]], elementi: list[dict], **extra) -> dict:
    return {'titolo': titolo, 'colonne': [{'chiave': c, 'etichetta': e} for c, e in colonne],
            'elementi': elementi, 'conteggio': len(elementi), **extra}


def _url(nome, pk) -> str:
    return url_assoluto(reverse(nome, args=[pk]))


def etichetta(tipo, doc) -> str:
    sez = getattr(doc, 'sezionale', '') or ''
    return f'{servizi.NOMI[tipo]} {sez + "/" if sez else ""}{doc.numero}/{doc.anno}'


def documento(tipo: str, doc, *, dettaglio=True) -> dict:
    from documenti.utils import calcola_valori

    v = calcola_valori(doc)
    d = {
        'tipo': tipo, 'id': doc.pk, 'numero': doc.numero, 'anno': doc.anno,
        'sezionale': getattr(doc, 'sezionale', ''),
        'etichetta': etichetta(tipo, doc),
        'data_documento': doc.data_documento.isoformat() if doc.data_documento else None,
        'cliente': doc.cliente.ragione_sociale if doc.cliente_id else None,
        'cliente_id': doc.cliente_id,
        'imponibile': _d(v['imponibile']), 'imposta': _d(v['imposta_totale']),
        'totale': _d(v['totale']),
        'url': _url(f'documenti:{tipo}_detail', doc.pk),
    }
    if tipo == 'fattura':
        d.update(pagata=doc.pagata, sdi_stato=doc.sdi_stato,
                 data_pagamento=doc.data_pagamento.isoformat() if doc.data_pagamento else None)
    elif hasattr(doc, 'get_stato_display'):
        d['stato'] = doc.get_stato_display()
    if dettaglio:
        d['forma_pagamento'] = str(doc.forme_pagamento) if doc.forme_pagamento_id else None
        d['note'] = doc.note or ''
        d['iva'] = [{'descrizione': x['descrizione'], 'imponibile': _d(x['imponibile']),
                     'imposta': _d(x['imposta'])} for x in v['iva_dettaglio']]
        d['righe'] = [{
            'descrizione': r.descrizione_libera or (str(r.articolo) if r.articolo_id else ''),
            'articolo': r.articolo.codice if r.articolo_id else None,
            'quantita': _d(r.quantita), 'um': r.um or '', 'prezzo': _d(r.importo_unitario),
            'iva': str(r.iva) if r.iva_id else None,
        } for r in doc.righe.select_related('iva', 'articolo')]
        if tipo == 'fattura':
            d['scadenze'] = [{'data': s.data.isoformat(), 'importo': _d(s.importo)}
                             for s in doc.scadenze.all()]
    return d


def _unico(qs, campo, descrizione, cosa, nome=str):
    trovati = list(qs[:20])
    if len(trovati) == 1:
        return trovati[0]
    if not trovati:
        raise DatiNonValidi({campo: f'Nessun {cosa} corrisponde a «{descrizione}».'})
    raise DatiNonValidi({campo: f'Più {cosa} corrispondono a «{descrizione}»: '
                                + ', '.join(f'{nome(o)} (id {o.pk})' for o in trovati[:6]) + '.'})


def _cliente(valore, campo='cliente', obbligatorio=True):
    errori = {}
    c = servizi.risolvi_cliente(valore, campo, errori, obbligatorio=obbligatorio)
    if errori:
        raise DatiNonValidi(errori)
    return c


def _fornitore(valore, campo='fornitore'):
    from anagrafiche.models import Fornitore

    if valore in (None, ''):
        raise DatiNonValidi({campo: 'Obbligatorio.'})
    pk = servizi._per_id(valore)
    if pk is not None:
        qs = Fornitore.objects.filter(pk=pk)
    else:
        testo = str(valore).strip()
        filtro = Q()
        for p in servizi._parole(testo)[:6]:
            filtro &= (Q(ragione_sociale__icontains=p) | Q(cognome__icontains=p) | Q(nome__icontains=p))
        qs = Fornitore.objects.filter(Q(partita_iva__iexact=testo.replace(' ', '')) | filtro)
    return _unico(qs.filter(soft_delete=False), campo, valore, 'fornitore')


# --------------------------------------------------------------------------- #
# Lettura
# --------------------------------------------------------------------------- #

@tool('cerca_clienti', 'Cerca clienti',
      "Clienti dell'anagrafica per ragione sociale (anche parziale), partita IVA o codice "
      'fiscale; senza testo, tutti (fino a 100). Dà anche forma di pagamento e IVA predefinite.',
      area='clienti', perm='clienti.vedi', proprieta={'testo': S})
def _cerca_clienti(a, user):
    elementi = [cliente_v2(c) for c in servizi.cerca_clienti(a.get('testo', ''))[:100]]
    return tabella('Clienti', [('ragione_sociale', 'Ragione sociale'), ('partita_iva', 'P.IVA'),
                               ('citta', 'Città'), ('stato', 'Stato')], elementi)


@tool('scheda_cliente', 'Scheda cliente',
      'Anagrafica completa di un cliente con contatti, fatturato dell\'anno, fatture da incassare '
      'e ultime fatture.',
      area='clienti', perm='clienti.vedi', proprieta={'cliente': CLIENTE}, obbligatori=('cliente',))
def _scheda_cliente(a, user):
    from documenti.models import TestataFattura, con_incassi

    from documenti.utils import calcola_valori

    c = _cliente(a.get('cliente'))
    fatture = TestataFattura.objects.filter(cliente=c)
    anno = now().year
    non_pagate = con_incassi(fatture.filter(pagata=False)).prefetch_related('righe__iva')
    # il totale su tutte le fatture aperte (meno gli incassi parziali),
    # l'elenco solo sulle prime 50
    da_incassare = sum((calcola_valori(f, righe=list(f.righe.all()))['totale'] - f.incassato_tot
                        for f in non_pagate), Decimal('0'))
    aperte = [documento('fattura', f, dettaglio=False) for f in non_pagate[:50]]
    return {
        **cliente_v2(c),
        'url': _url('clienti:detail', c.pk),
        'contatti': [{'nome': f'{x.nome} {x.cognome}'.strip(), 'ruolo': x.ruolo, 'email': x.email,
                      'telefono': x.telefono} for x in c.contatti.all()],
        'fatturato_anno': _d(fatture.filter(anno=anno).aggregate(s=Sum('imponibile'))['s']
                             or Decimal('0')),
        'da_incassare': _d(da_incassare),
        'fatture_non_pagate': aperte,
        'ultime_fatture': [documento('fattura', f, dettaglio=False) for f in fatture[:5]],
    }


@tool('elenco_documenti', 'Elenco documenti',
      'Fatture, offerte, ordini, DDT o note di credito, filtrati per cliente, periodo, anno e (per '
      'le fatture) pagate o no. Dal più recente.',
      area='documenti', perm='documenti.vedi',
      proprieta={'tipo': {'type': 'string', 'enum': TIPI_DOC}, 'cliente': CLIENTE,
                 'da': DATA, 'a': DATA, 'anno': ANNO,
                 'pagata': {'type': 'boolean', 'description': 'Solo fatture: true pagate, false da pagare.'},
                 'limite': {'type': 'integer', 'description': 'Quanti al massimo (50).'}},
      obbligatori=('tipo',))
def _elenco_documenti(a, user):
    tipo = a.get('tipo')
    if tipo not in servizi.MODELLI:
        raise DatiNonValidi({'tipo': f'Documento sconosciuto: {tipo}.'})
    qs = servizi.MODELLI[tipo].objects.select_related('cliente').order_by('-anno', '-numero')
    if a.get('cliente') not in (None, ''):
        qs = qs.filter(cliente=_cliente(a['cliente']))
    anno = _intero(a.get('anno'), 'anno')
    if anno:
        qs = qs.filter(anno=anno)
    da, al = _giorno(a.get('da'), 'da'), _giorno(a.get('a'), 'a')
    if da:
        qs = qs.filter(data_documento__gte=da)
    if al:
        qs = qs.filter(data_documento__lte=al)
    if tipo == 'fattura' and isinstance(a.get('pagata'), bool):
        qs = qs.filter(pagata=a['pagata'])
    limite = _intero(a.get('limite'), 'limite', predefinito=50, minimo=1, massimo=200)
    elementi = [documento(tipo, d, dettaglio=False) for d in qs[:limite]]
    colonne = [('etichetta', 'Documento'), ('data_documento', 'Data'), ('cliente', 'Cliente'),
               ('imponibile', 'Imponibile €'), ('totale', 'Totale €')]
    colonne.append(('pagata', 'Pagata') if tipo == 'fattura' else ('stato', 'Stato'))
    return tabella(f'{servizi.NOMI[tipo]}: elenco', colonne, elementi,
                   totale=_d(sum((Decimal(e['totale']) for e in elementi), Decimal('0'))))


@tool('dettaglio_documento', 'Dettaglio documento',
      'Un documento con righe, IVA, totali e (per le fatture) scadenze e stato di pagamento e SDI.',
      area='documenti', perm='documenti.vedi',
      proprieta={'tipo': {'type': 'string', 'enum': TIPI_DOC}, 'numero': NUMERO, 'anno': ANNO,
                 'sezionale': SEZIONALE},
      obbligatori=('tipo', 'numero'))
def _dettaglio_documento(a, user):
    doc = servizi.trova_documento(a.get('tipo'), a.get('numero'), a.get('anno'), a.get('sezionale', ''))
    return documento(a['tipo'], doc)


@tool('scadenziario_attivo', 'Scadenziario clienti',
      'Le rate delle fatture di vendita ancora da incassare, con i giorni alla scadenza (negativi = '
      'scadute) e i totali scaduto / entro 30 giorni / oltre.',
      area='incassi', perm='documenti.vedi', proprieta={'cliente': CLIENTE, 'fino_al': DATA})
def _scadenziario_attivo(a, user):
    from documenti.models import ScadenzaFattura, crediti_aperti

    oggi = localdate()
    # il residuo: un incasso parziale riduce il credito
    qs = crediti_aperti(ScadenzaFattura.objects.select_related('fattura__cliente')).order_by('data')
    if a.get('cliente') not in (None, ''):
        qs = qs.filter(fattura__cliente=_cliente(a['cliente']))
    fino = _giorno(a.get('fino_al'), 'fino_al')
    if fino:
        qs = qs.filter(data__lte=fino)
    # totali sul DB, su tutte le rate; l'elenco si ferma alle prime 500
    fra_30 = oggi + timedelta(days=30)
    tot = qs.aggregate(
        scaduto=Sum('residuo_db', filter=Q(data__lt=oggi)),
        entro_30_giorni=Sum('residuo_db', filter=Q(data__gte=oggi, data__lte=fra_30)),
        oltre_30_giorni=Sum('residuo_db', filter=Q(data__gt=fra_30)),
        totale=Sum('residuo_db'))
    tot = {k: v or Decimal('0') for k, v in tot.items()}
    elementi = []
    for s in qs[:500]:
        giorni = (s.data - oggi).days
        elementi.append({'data': s.data.isoformat(), 'giorni': giorni,
                         'fattura': etichetta('fattura', s.fattura),
                         'cliente': s.fattura.cliente.ragione_sociale if s.fattura.cliente_id else '',
                         'importo': _d(s.importo), 'residuo': _d(s.residuo_db)})
    return tabella('Scadenziario clienti', [('data', 'Scadenza'), ('giorni', 'Giorni'),
                   ('fattura', 'Fattura'), ('cliente', 'Cliente'), ('residuo', 'Da incassare €')],
                   elementi, totali={k: _d(v) for k, v in tot.items()})


@tool('scadenziario_passivo', 'Scadenziario fornitori',
      'Le rate delle fatture di acquisto ancora da pagare, con il residuo e i giorni alla scadenza.',
      area='acquisti', perm='documenti.vedi', proprieta={'fornitore': S, 'fino_al': DATA})
def _scadenziario_passivo(a, user):
    from documenti.models import ScadenzaFatturaAcquisto

    oggi = localdate()
    zero = Value(Decimal('0'), output_field=DecimalField(max_digits=12, decimal_places=2))
    qs = ScadenzaFatturaAcquisto.objects.select_related('fattura__fornitore').filter(
        fattura__pagata=False).annotate(
        pagato=Coalesce(Sum('pagamenti__importo'), zero)).order_by('data_scadenza')
    if a.get('fornitore') not in (None, ''):
        qs = qs.filter(fattura__fornitore=_fornitore(a['fornitore']))
    fino = _giorno(a.get('fino_al'), 'fino_al')
    if fino:
        qs = qs.filter(data_scadenza__lte=fino)
    # il totale su tutte le rate aperte (una query, pagato annotato);
    # l'elenco si ferma alle prime 500
    elementi, totale = [], Decimal('0')
    for s in qs:
        residuo = s.importo - s.pagato
        if residuo <= 0:
            continue
        totale += residuo
        if len(elementi) >= 500:
            continue
        f = s.fattura
        elementi.append({'data': s.data_scadenza.isoformat(), 'giorni': (s.data_scadenza - oggi).days,
                         'fornitore': str(f.fornitore) if f.fornitore_id else '',
                         'fattura': f.numero_fornitore or f'{f.numero}/{f.anno}',
                         'importo': _d(s.importo), 'residuo': _d(residuo)})
    return tabella('Scadenziario fornitori', [('data', 'Scadenza'), ('giorni', 'Giorni'),
                   ('fornitore', 'Fornitore'), ('fattura', 'Fattura'), ('residuo', 'Da pagare €')],
                   elementi, totale=_d(totale))


@tool('fatture_acquisto', 'Fatture di acquisto',
      'Le fatture dei fornitori, per fornitore, periodo e da pagare o no.',
      area='acquisti', perm='documenti.vedi',
      proprieta={'fornitore': S, 'da': DATA, 'a': DATA, 'da_pagare': {'type': 'boolean'}})
def _fatture_acquisto(a, user):
    from documenti.models import TestataFatturaAcquisto

    qs = TestataFatturaAcquisto.objects.select_related('fornitore').order_by('-data_documento', '-pk')
    if a.get('fornitore') not in (None, ''):
        qs = qs.filter(fornitore=_fornitore(a['fornitore']))
    da, al = _giorno(a.get('da'), 'da'), _giorno(a.get('a'), 'a')
    if da:
        qs = qs.filter(data_documento__gte=da)
    if al:
        qs = qs.filter(data_documento__lte=al)
    if isinstance(a.get('da_pagare'), bool):
        qs = qs.filter(pagata=not a['da_pagare'])
    elementi = [{'id': f.pk, 'fornitore': str(f.fornitore) if f.fornitore_id else '',
                 'numero_fornitore': f.numero_fornitore or '',
                 'data': (f.data_fornitore or f.data_documento).isoformat() if (f.data_fornitore or f.data_documento) else None,
                 'totale': _d(f.totale_documento), 'pagata': f.pagata,
                 'url': _url('documenti:fattura_acquisto_detail', f.pk)} for f in qs[:200]]
    return tabella('Fatture di acquisto', [('fornitore', 'Fornitore'), ('numero_fornitore', 'Numero'),
                   ('data', 'Data'), ('totale', 'Totale €'), ('pagata', 'Pagata')], elementi)


@tool('fatturato', 'Fatturato',
      "Il fatturato di un anno (imponibile delle fatture meno le note di credito), mese per mese, "
      'e i primi 10 clienti.',
      area='documenti', perm='report.vedi', proprieta={'anno': ANNO})
def _fatturato(a, user):
    from documenti.models import TestataFattura, TestataNotaCredito

    anno = _intero(a.get('anno'), 'anno', predefinito=now().year)
    mesi = []
    for m in range(1, 13):
        f = TestataFattura.objects.filter(data_documento__year=anno, data_documento__month=m) \
            .aggregate(s=Sum('imponibile'))['s'] or Decimal('0')
        nc = TestataNotaCredito.objects.filter(data_documento__year=anno, data_documento__month=m) \
            .aggregate(s=Sum('imponibile'))['s'] or Decimal('0')
        mesi.append({'mese': f'{anno}-{m:02d}', 'fatture': _d(f), 'note_credito': _d(nc),
                     'netto': _d(f - nc)})
    top = (TestataFattura.objects.filter(data_documento__year=anno, cliente__isnull=False)
           .values('cliente__ragione_sociale').annotate(s=Sum('imponibile')).order_by('-s')[:10])
    return tabella(f'Fatturato {anno}', [('mese', 'Mese'), ('fatture', 'Fatture €'),
                   ('note_credito', 'Note di credito €'), ('netto', 'Netto €')], mesi,
                   totale_netto=_d(sum((Decimal(x['netto']) for x in mesi), Decimal('0'))),
                   primi_clienti=[{'cliente': t['cliente__ragione_sociale'], 'imponibile': _d(t['s'])}
                                  for t in top])


@tool('giacenze', 'Giacenze di magazzino',
      "Per articolo: caricato, scaricato, giacenza e quantità impegnata dagli ordini. Con "
      "«articolo» (codice o descrizione) solo quelli che corrispondono.",
      area='magazzino', perm='documenti.vedi', proprieta={'articolo': S})
def _giacenze(a, user):
    from anagrafiche.models import Articolo
    from magazzino.models import Movimento

    qs = Articolo.objects.filter(obsoleto=False).order_by('codice')
    testo = str(a.get('articolo') or '').strip()
    if testo:
        qs = qs.filter(Q(codice__icontains=testo) | Q(descrizione__icontains=testo))
    # le somme per tipo di movimento in una sola query per tutti gli articoli
    ids = list(qs.values_list('pk', flat=True)[:300])
    per_articolo = {}
    for riga in (Movimento.objects.filter(articolo_id__in=ids, tipo__in=('C', 'S', 'I'))
                 .values('articolo_id', 'tipo').annotate(s=Sum('quantita'))):
        per_articolo.setdefault(riga['articolo_id'], {})[riga['tipo']] = riga['s'] or Decimal('0')
    elementi = []
    for art in Articolo.objects.filter(pk__in=ids).order_by('codice'):
        somme = {t: per_articolo.get(art.pk, {}).get(t, Decimal('0')) for t in ('C', 'S', 'I')}
        elementi.append({'codice': art.codice, 'descrizione': art.descrizione, 'um': art.um,
                         'caricato': _d(somme['C']), 'scaricato': _d(somme['S']),
                         'giacenza': _d(somme['C'] - somme['S']), 'impegnato': _d(somme['I'])})
    return tabella('Giacenze', [('codice', 'Codice'), ('descrizione', 'Descrizione'),
                   ('giacenza', 'Giacenza'), ('impegnato', 'Impegnato'), ('um', 'UM')], elementi)


@tool('cerca_articoli', 'Cerca articoli',
      "Articoli di magazzino per codice o descrizione, con prezzo di listino e unità di misura.",
      area='magazzino', perm='documenti.vedi', proprieta={'testo': S})
def _cerca_articoli(a, user):
    from anagrafiche.models import Articolo

    testo = str(a.get('testo') or '').strip()
    qs = Articolo.objects.filter(obsoleto=False).order_by('codice')
    if testo:
        qs = qs.filter(Q(codice__icontains=testo) | Q(descrizione__icontains=testo))
    elementi = [{'codice': x.codice, 'descrizione': x.descrizione, 'um': x.um,
                 'prezzo_listino': _d(x.prezzo_listino)} for x in qs[:100]]
    return tabella('Articoli', [('codice', 'Codice'), ('descrizione', 'Descrizione'),
                   ('prezzo_listino', 'Listino €'), ('um', 'UM')], elementi)


@tool('elenco_opportunita', 'Opportunità',
      'Le opportunità commerciali (pipeline), per stadio e cliente, con valore e chiusura prevista.',
      area='crm', perm='clienti.vedi',
      proprieta={'stadio': {'type': 'string', 'enum': ['nuova', 'qualificata', 'proposta',
                                                        'negoziazione', 'chiusa_win', 'chiusa_lost']},
                 'cliente': CLIENTE})
def _elenco_opportunita(a, user):
    from opportunita.models import Opportunita

    qs = Opportunita.objects.select_related('cliente').order_by('-updated_at')
    if a.get('stadio'):
        qs = qs.filter(stadio=a['stadio'])
    if a.get('cliente') not in (None, ''):
        qs = qs.filter(cliente=_cliente(a['cliente']))
    elementi = [{'id': o.pk, 'titolo': o.titolo, 'cliente': o.cliente.ragione_sociale,
                 'valore': _d(o.valore), 'stadio': o.get_stadio_display(),
                 'probabilita': o.probabilita,
                 'chiusura_prevista': o.chiusura_prevista.isoformat() if o.chiusura_prevista else None,
                 'url': _url('opportunita:detail', o.pk)} for o in qs[:200]]
    return tabella('Opportunità', [('titolo', 'Titolo'), ('cliente', 'Cliente'), ('valore', 'Valore €'),
                   ('stadio', 'Stadio'), ('chiusura_prevista', 'Chiusura')], elementi,
                   totale=_d(sum((Decimal(e['valore']) for e in elementi), Decimal('0'))))


@tool('elenco_attivita', 'Attività',
      'Chiamate, email, meeting e task del CRM in un periodo, completate o da fare.',
      area='crm', perm='clienti.vedi',
      proprieta={'da': DATA, 'a': DATA, 'completate': {'type': 'boolean'}, 'cliente': CLIENTE})
def _elenco_attivita(a, user):
    from attivita.models import Attivita

    qs = Attivita.objects.select_related('cliente').order_by('data')
    da, al = _giorno(a.get('da'), 'da'), _giorno(a.get('a'), 'a')
    if da:
        qs = qs.filter(data__date__gte=da)
    if al:
        qs = qs.filter(data__date__lte=al)
    if isinstance(a.get('completate'), bool):
        qs = qs.filter(completata=a['completate'])
    if a.get('cliente') not in (None, ''):
        qs = qs.filter(cliente=_cliente(a['cliente']))
    elementi = [{'id': x.pk, 'data': timezone.localtime(x.data).strftime('%Y-%m-%d %H:%M'),
                 'tipo': x.get_tipo_display(), 'titolo': x.titolo,
                 'cliente': x.cliente.ragione_sociale if x.cliente_id else '',
                 'completata': x.completata} for x in qs[:300]]
    return tabella('Attività', [('data', 'Quando'), ('tipo', 'Tipo'), ('titolo', 'Titolo'),
                   ('cliente', 'Cliente'), ('completata', 'Fatta')], elementi)


@tool('scadenze_fiscali', 'Scadenze fiscali',
      'IVA, F24, INPS e le altre scadenze fiscali inserite, in un periodo, pagate o no; più le '
      'rate delle spese ricorrenti.',
      area='cashflow', perm='cashflow.vedi',
      proprieta={'da': DATA, 'a': DATA, 'pagate': {'type': 'boolean'}})
def _scadenze_fiscali(a, user):
    from cashflow.models import ScadenzaFiscale, ScadenzaSpesa

    da, al = _giorno(a.get('da'), 'da'), _giorno(a.get('a'), 'a')
    elementi = []
    for model, cosa, nome in ((ScadenzaFiscale, 'fiscale', lambda s: f'{s.get_tipo_display()} — {s.descrizione}'),
                              (ScadenzaSpesa, 'spesa', lambda s: s.spesa.descrizione)):
        qs = model.objects.order_by('data_scadenza')
        if da:
            qs = qs.filter(data_scadenza__gte=da)
        if al:
            qs = qs.filter(data_scadenza__lte=al)
        if isinstance(a.get('pagate'), bool):
            qs = qs.filter(pagata=a['pagate'])
        elementi += [{'data': s.data_scadenza.isoformat(), 'tipo': cosa, 'descrizione': nome(s),
                      'importo': _d(s.importo), 'pagata': s.pagata} for s in qs[:300]]
    elementi.sort(key=lambda e: e['data'])
    return tabella('Scadenze fiscali e spese', [('data', 'Scadenza'), ('descrizione', 'Descrizione'),
                   ('importo', 'Importo €'), ('pagata', 'Pagata')], elementi,
                   totale=_d(sum((Decimal(e['importo']) for e in elementi if not e['pagata']), Decimal('0'))))


@tool('tabelle_fatturazione', 'Tabelle di fatturazione',
      'Posizioni IVA, forme di pagamento e conti correnti dell\'azienda: i valori ammessi nei documenti.',
      area='documenti', perm='documenti.vedi')
def _tabelle(a, user):
    from anagrafiche.models import ContoCorrente, FormePagamento, PosizioneIva

    return {
        'posizioni_iva': [{'id': p.pk, 'descrizione': p.descrizione, 'aliquota': _d(p.aliquota),
                           'natura': p.natura or ''} for p in PosizioneIva.objects.all()],
        'forme_pagamento': [{'id': f.pk, 'nome': str(f)} for f in FormePagamento.objects.order_by('pk')],
        'conti_correnti': [{'id': c.pk, 'banca': c.banca, 'iban': c.iban}
                           for c in ContoCorrente.objects.filter(anagrafica_azienda__isnull=False)],
    }


# --------------------------------------------------------------------------- #
# Scrittura: documenti
# --------------------------------------------------------------------------- #

FINALE = {'fattura': 'a', 'offerta': 'a', 'nota_credito': 'a', 'ordine': 'o', 'ddt': 'o'}


def _crea(tipo):
    def esegui(a, user):
        doc = servizi.crea_documento(tipo, a)
        return {**documento(tipo, doc), 'messaggio': f'{etichetta(tipo, doc)} creat{FINALE[tipo]}.'}
    return esegui


for _tipo, _titolo, _descr, _extra in (
    ('fattura', 'Crea fattura',
     'Emette una fattura (TD01 immediata, non inviata allo SDI) a un cliente. Prende il prossimo '
     'numero progressivo. IVA e pagamento, se mancano, sono quelli del cliente.',
     {'tipo_documento': {'type': 'string', 'description': 'TD01 (predefinito), TD24 differita…'},
      'sezionale': SEZIONALE}),
    ('offerta', 'Crea offerta', 'Prepara un\'offerta (preventivo) per un cliente.',
     {'scadenza': {'type': 'string', 'description': 'Validità dell\'offerta, AAAA-MM-GG.'}}),
    ('ordine', 'Crea ordine cliente', 'Registra un ordine ricevuto da un cliente.', {}),
    ('ddt', 'Crea DDT', 'Documento di trasporto per merce consegnata a un cliente. I prezzi possono mancare.',
     {'causale_trasporto': {'type': 'string', 'description': 'Es. «Vendita», «Conto visione».'},
      'porto': {'type': 'string', 'description': 'Es. «Franco», «Assegnato».'},
      'aspetto_beni': S, 'vettore': S, 'numero_palette': {'type': 'integer'},
      'peso_netto': {'type': 'number'}, 'peso_lordo': {'type': 'number'}}),
):
    REGISTRO[f'crea_{_tipo}'] = Tool(
        f'crea_{_tipo}', _titolo, _descr, 'documenti', {**DOC_COMUNI, **_extra},
        ('cliente', 'righe'), _crea(_tipo), 'documenti.modifica', lettura=False)


@tool('converti_documento', 'Genera documento dal precedente',
      "Il passo successivo del ciclo: un'offerta diventa ordine, un ordine diventa DDT o fattura, "
      'una fattura diventa nota di credito (storno). Copia cliente e righe.',
      area='documenti', perm='documenti.modifica', lettura=False,
      proprieta={'conversione': {'type': 'string', 'enum': list(servizi.CONVERSIONI)},
                 'numero': NUMERO, 'anno': ANNO, 'sezionale': SEZIONALE},
      obbligatori=('conversione', 'numero'))
def _converti(a, user):
    verso = a.get('conversione')
    if verso not in servizi.CONVERSIONI:
        raise DatiNonValidi({'conversione': f'Conversione sconosciuta: {verso}.'})
    da, verso_tipo = servizi.CONVERSIONI[verso]
    origine = servizi.trova_documento(da, a.get('numero'), a.get('anno'), a.get('sezionale', ''))
    nuovo = servizi.converti(verso, origine)
    return {**documento(verso_tipo, nuovo), 'origine': etichetta(da, origine),
            'messaggio': f'{etichetta(verso_tipo, nuovo)} generat{FINALE[verso_tipo]} da '
                         f'{etichetta(da, origine)}.'}


@tool('segna_fattura_pagata', 'Segna fattura pagata',
      'Registra che una fattura di vendita è stata incassata (oggi, se non si dice la data).',
      area='incassi', perm='documenti.modifica', lettura=False,
      proprieta={'numero': NUMERO, 'anno': ANNO, 'sezionale': SEZIONALE, 'data_pagamento': DATA},
      obbligatori=('numero',))
def _segna_pagata(a, user):
    f = servizi.trova_documento('fattura', a.get('numero'), a.get('anno'), a.get('sezionale', ''))
    servizi.segna_pagata(f, a.get('data_pagamento'))
    return {**documento('fattura', f, dettaglio=False),
            'messaggio': f'{etichetta("fattura", f)} segnata pagata il {f.data_pagamento:%d/%m/%Y}.'}


@tool('registra_incasso', 'Registra incasso',
      "Registra un incasso, anche parziale, su una fattura di vendita: copre le rate aperte dalla "
      "prima fino all'importo (tutto il residuo, se l'importo manca).",
      area='incassi', perm='documenti.modifica', lettura=False,
      proprieta={'numero': NUMERO, 'anno': ANNO, 'sezionale': SEZIONALE,
                 'importo': {'type': 'number'}, 'data': DATA},
      obbligatori=('numero',))
def _registra_incasso(a, user):
    f = servizi.trova_documento('fattura', a.get('numero'), a.get('anno'), a.get('sezionale', ''))
    incassi = servizi.incassa(f, a.get('importo'), a.get('data'))
    totale = sum((i.importo for i in incassi), Decimal('0'))
    return {**documento('fattura', f, dettaglio=False),
            'incassato': _d(totale),
            'rate': [{'scadenza': i.scadenza.data.isoformat(), 'importo': _d(i.importo)} for i in incassi],
            'messaggio': f'Registrato incasso di {totale} € su {etichetta("fattura", f)}'
                         + (': fattura saldata.' if f.pagata else '.')}


@tool('registra_pagamento_fornitore', 'Registra pagamento a fornitore',
      "Registra il pagamento di una fattura di acquisto: salda le rate aperte fino all'importo "
      '(tutto il residuo, se l\'importo manca).',
      area='acquisti', perm='documenti.modifica', lettura=False,
      proprieta={'fornitore': S, 'numero_fornitore': {'type': 'string', 'description':
                 'Il numero della fattura scritto dal fornitore.'},
                 'importo': {'type': 'number'}, 'data_pagamento': DATA},
      obbligatori=('fornitore', 'numero_fornitore'))
def _paga_fornitore(a, user):
    from documenti.models import PagamentoScadenzaAcquisto, TestataFatturaAcquisto

    fornitore = _fornitore(a.get('fornitore'))
    fattura = _unico(TestataFatturaAcquisto.objects.filter(
        fornitore=fornitore, numero_fornitore__iexact=str(a.get('numero_fornitore') or '').strip()),
        'numero_fornitore', a.get('numero_fornitore'), 'fattura di acquisto')
    giorno = _giorno(a.get('data_pagamento'), 'data_pagamento', predefinito=localdate())
    residuo_tot = sum((s.importo_residuo for s in fattura.scadenze.all()), Decimal('0'))
    if residuo_tot <= 0:
        raise DatiNonValidi({'numero_fornitore': 'La fattura non ha rate da pagare.'})
    errori = {}
    importo = servizi._decimale(a.get('importo'), 'importo', errori, predefinito=residuo_tot)
    if errori:
        raise DatiNonValidi(errori)
    if importo > residuo_tot:
        raise DatiNonValidi({'importo': f'Il residuo da pagare è {residuo_tot} €.'})
    resta, pagate = importo, []
    for s in fattura.scadenze.order_by('data_scadenza'):
        if resta <= 0:
            break
        quota = min(resta, s.importo_residuo)
        if quota <= 0:
            continue
        PagamentoScadenzaAcquisto.objects.create(scadenza=s, data_pagamento=giorno, importo=quota)
        pagate.append({'scadenza': s.data_scadenza.isoformat(), 'importo': _d(quota)})
        resta -= quota
    fattura.refresh_from_db()
    return {'fornitore': str(fornitore), 'numero_fornitore': fattura.numero_fornitore,
            'pagato': _d(importo), 'rate': pagate, 'fattura_saldata': fattura.pagata,
            'url': _url('documenti:fattura_acquisto_detail', fattura.pk),
            'messaggio': f'Registrato pagamento di {importo} € a {fornitore}.'}


# --------------------------------------------------------------------------- #
# Scrittura: clienti e CRM
# --------------------------------------------------------------------------- #

@tool('crea_cliente', 'Crea cliente',
      "Inserisce un nuovo cliente nell'anagrafica. La partita IVA è obbligatoria (11 cifre); il "
      'gestionale controlla formato, CAP e doppioni.',
      area='clienti', perm='clienti.modifica', lettura=False,
      proprieta={'ragione_sociale': S, 'tipo': {'type': 'string', 'enum': ['azienda', 'privato', 'ente']},
                 'partita_iva': S, 'codice_fiscale': S, 'codice_sdi': S, 'pec': S, 'indirizzo': S,
                 'cap': S, 'citta': S, 'provincia': S, 'nazione': S, 'note': S,
                 'ref_nome': S, 'ref_cognome': S, 'ref_email': S, 'ref_tel': S},
      obbligatori=('ragione_sociale', 'partita_iva'))
def _crea_cliente(a, user):
    from clienti.models import Cliente

    from .v2 import _avvisi_cliente, _form_cliente, _valida_cliente

    form = _form_cliente(a)
    errori = _valida_cliente(form)
    if errori:
        raise DatiNonValidi(errori)
    c = form.save()
    c = Cliente.objects.select_related('settore', 'forme_pagamento', 'posizione_iva').get(pk=c.pk)
    return {**cliente_v2(c), 'url': _url('clienti:detail', c.pk), 'avvisi': _avvisi_cliente(form),
            'messaggio': f'Cliente «{c.ragione_sociale}» creato.'}


# --------------------------------------------------------------------------- #
# Scrittura: anagrafiche
# --------------------------------------------------------------------------- #

MAX_RATE = 12


def _modalita():
    from anagrafiche.models import FormePagamento
    return list(FormePagamento.ModalitaPagamentoType.choices)


_MODALITA_PAGAMENTO = _modalita()


def _percentuale(valore, campo):
    try:
        p = Decimal(str(valore).strip().replace(',', '.'))
    except (ArithmeticError, ValueError, TypeError):
        raise DatiNonValidi({campo: 'Deve essere una percentuale (es. 50).'}) from None
    if not p.is_finite() or p <= 0 or p > 100:
        raise DatiNonValidi({campo: 'Deve essere maggiore di 0 e al massimo 100.'})
    return p.quantize(Decimal('0.01'))


def _rate(grezze):
    """Le rate della forma di pagamento, validate: giorni ≥ 0, percentuali che
    sommano a 100. Nessuna rata = un'unica scadenza a vista (0 giorni, 100%)."""
    if grezze in (None, '', []):
        return [{'giorni': 0, 'percentuale': Decimal('100.00'), 'fine_mese': False,
                 'giorni_dopo_fine_mese': 0}]
    if not isinstance(grezze, list) or not all(isinstance(r, dict) for r in grezze):
        raise DatiNonValidi({'rate': 'Elenco di rate: [{"giorni": 30, "percentuale": 100}].'})
    if len(grezze) > MAX_RATE:
        raise DatiNonValidi({'rate': f'Al massimo {MAX_RATE} rate.'})
    rate = []
    for i, r in enumerate(grezze, start=1):
        giorni = _intero(r.get('giorni'), f'rate.{i}.giorni', predefinito=0)
        dopo = _intero(r.get('giorni_dopo_fine_mese'), f'rate.{i}.giorni_dopo_fine_mese',
                       predefinito=0)
        if giorni < 0 or giorni > 365 or dopo < 0 or dopo > 31:
            raise DatiNonValidi({f'rate.{i}': 'Giorni fra 0 e 365 (dopo fine mese fra 0 e 31).'})
        rate.append({'giorni': giorni,
                     'percentuale': _percentuale(r.get('percentuale', 100), f'rate.{i}.percentuale'),
                     'fine_mese': bool(r.get('fine_mese')), 'giorni_dopo_fine_mese': dopo})
    totale = sum(r['percentuale'] for r in rate)
    if abs(totale - 100) > Decimal('0.01'):
        raise DatiNonValidi({'rate': f'Le percentuali devono sommare a 100 (ora {totale}).'})
    return sorted(rate, key=lambda r: r['giorni'])


def _forma_pagamento(f) -> dict:
    return {'id': f.pk, 'nome': str(f), 'modalita': f.modalita_pagamento,
            'modalita_descrizione': f.get_modalita_pagamento_display(),
            'rate': [{'giorni': s.numero_giorni, 'percentuale': str(s.percentuale),
                      'fine_mese': s.fine_mese, 'giorni_dopo_fine_mese': s.numero_giorni_fm}
                     for s in f.scadenze.order_by('numero_giorni')],
            'url': _url('anagrafiche:forma_pagamento_update', f.pk)}


@tool('crea_forma_pagamento', 'Crea forma di pagamento',
      'Aggiunge una forma di pagamento (es. «Bonifico 30/60 gg DF», «Riba 60 gg FM») con le sue '
      'rate: per ognuna i giorni dalla data fattura, la percentuale del totale e se la scadenza '
      'cade a fine mese (più eventuali giorni dopo il fine mese). Senza rate: pagamento a vista. '
      'Poi si può usare nelle fatture e assegnare ai clienti.',
      area='anagrafiche', perm='clienti.modifica', lettura=False,
      proprieta={
          'nome': {'type': 'string', 'description': 'Come comparirà nei menu, es. «Bonifico 30 gg DF».'},
          'modalita': {'type': 'string',
                       'enum': [c for c, _ in _MODALITA_PAGAMENTO],
                       'description': 'Codice FatturaPA: ' + ', '.join(
                           f'{c} {n}' for c, n in _MODALITA_PAGAMENTO) + '. Predefinito MP05.'},
          'rate': {'type': 'array', 'items': {
              'type': 'object',
              'properties': {
                  'giorni': {'type': 'integer', 'description': 'Giorni dalla data fattura (0 = a vista).'},
                  'percentuale': {'type': 'number', 'description': 'Quota del totale, es. 50.'},
                  'fine_mese': {'type': 'boolean', 'description': 'Scadenza spostata a fine mese.'},
                  'giorni_dopo_fine_mese': {'type': 'integer',
                                            'description': 'Giorni da aggiungere dopo il fine mese.'}},
              'required': ['giorni', 'percentuale']},
              'description': 'Le rate; le percentuali sommano a 100.'}},
      obbligatori=('nome',))
def _crea_forma_pagamento(a, user):
    from anagrafiche.models import FormePagamento, Scadenza

    nome = ' '.join(str(a.get('nome') or '').split())[:50]
    if not nome:
        raise DatiNonValidi({'nome': 'Obbligatorio.'})
    if FormePagamento.objects.filter(tipo_pagamento__iexact=nome).exists():
        raise DatiNonValidi({'nome': f'Esiste già una forma di pagamento «{nome}».'})
    modalita = str(a.get('modalita') or 'MP05').strip().upper()
    if modalita not in dict(_MODALITA_PAGAMENTO):
        raise DatiNonValidi({'modalita': f'Codice non valido: {modalita}.'})
    rate = _rate(a.get('rate'))
    f = FormePagamento.objects.create(tipo_pagamento=nome, modalita_pagamento=modalita)
    Scadenza.objects.bulk_create([
        Scadenza(forme_pagamento=f, numero_giorni=r['giorni'], percentuale=r['percentuale'],
                 fine_mese=r['fine_mese'], numero_giorni_fm=r['giorni_dopo_fine_mese'])
        for r in rate])
    return {**_forma_pagamento(f), 'messaggio': f'Forma di pagamento «{nome}» creata.'}


def _quando(valore, campo):
    if valore in (None, ''):
        raise DatiNonValidi({campo: 'Obbligatorio.'})
    try:
        d = datetime.fromisoformat(str(valore))
    except ValueError:
        raise DatiNonValidi({campo: 'Data non valida (AAAA-MM-GG o AAAA-MM-GGTHH:MM).'}) from None
    if len(str(valore)) <= 10:
        d = datetime.combine(d.date(), time(9, 0))
    return timezone.make_aware(d) if timezone.is_naive(d) else d


@tool('crea_attivita', 'Crea attività',
      'Una chiamata, email, meeting o task da fare nel CRM, eventualmente legata a un cliente.',
      area='crm', perm='attivita.modifica', lettura=False,
      proprieta={'titolo': S, 'tipo': {'type': 'string', 'enum': ['call', 'email', 'meeting', 'task', 'note']},
                 'data': {'type': 'string', 'description': 'AAAA-MM-GG o AAAA-MM-GGTHH:MM.'},
                 'durata_minuti': {'type': 'integer'}, 'cliente': CLIENTE, 'descrizione': S},
      obbligatori=('titolo', 'data'))
def _crea_attivita(a, user):
    from attivita.models import Attivita, TipoAttivita

    tipo = a.get('tipo') or TipoAttivita.TASK
    if tipo not in TipoAttivita.values:
        raise DatiNonValidi({'tipo': f'Tipo sconosciuto: {tipo}.'})
    titolo = str(a.get('titolo') or '').strip()[:200]
    if not titolo:
        raise DatiNonValidi({'titolo': 'Obbligatorio.'})
    x = Attivita.objects.create(
        titolo=titolo, tipo=tipo, data=_quando(a.get('data'), 'data'),
        durata_minuti=_intero(a.get('durata_minuti'), 'durata_minuti', predefinito=30,
                              minimo=5, massimo=24 * 60),
        cliente=_cliente(a.get('cliente'), obbligatorio=False),
        descrizione=str(a.get('descrizione') or ''), owner=user)
    return {'id': x.pk, 'titolo': x.titolo, 'tipo': x.get_tipo_display(),
            'data': timezone.localtime(x.data).strftime('%Y-%m-%d %H:%M'),
            'cliente': x.cliente.ragione_sociale if x.cliente_id else None,
            'url': _url('attivita:detail', x.pk), 'messaggio': f'Attività «{x.titolo}» creata.'}


STADI = ['nuova', 'qualificata', 'proposta', 'negoziazione', 'chiusa_win', 'chiusa_lost']


@tool('crea_opportunita', 'Crea opportunità',
      'Una nuova opportunità commerciale nella pipeline di un cliente.',
      area='crm', perm='opportunita.modifica', lettura=False,
      proprieta={'titolo': S, 'cliente': CLIENTE, 'valore': {'type': 'number'},
                 'stadio': {'type': 'string', 'enum': STADI},
                 'probabilita': {'type': 'integer', 'description': '0-100'},
                 'chiusura_prevista': DATA, 'descrizione': S},
      obbligatori=('titolo', 'cliente'))
def _crea_opportunita(a, user):
    from opportunita.models import Opportunita

    errori = {}
    valore = servizi._decimale(a.get('valore'), 'valore', errori, predefinito=Decimal('0'),
                               positivo=False)
    stadio = a.get('stadio') or 'nuova'
    if stadio not in STADI:
        errori['stadio'] = f'Stadio sconosciuto: {stadio}.'
    titolo = str(a.get('titolo') or '').strip()[:200]
    if not titolo:
        errori['titolo'] = 'Obbligatorio.'
    cliente = servizi.risolvi_cliente(a.get('cliente'), 'cliente', errori)
    if errori:
        raise DatiNonValidi(errori)
    o = Opportunita.objects.create(
        titolo=titolo, cliente=cliente, valore=valore, stadio=stadio,
        probabilita=_intero(a.get('probabilita'), 'probabilita', predefinito=25, minimo=0,
                            massimo=100),
        chiusura_prevista=_giorno(a.get('chiusura_prevista'), 'chiusura_prevista'),
        descrizione=str(a.get('descrizione') or ''), owner=user)
    return {'id': o.pk, 'titolo': o.titolo, 'cliente': cliente.ragione_sociale, 'valore': _d(o.valore),
            'stadio': o.get_stadio_display(), 'url': _url('opportunita:detail', o.pk),
            'messaggio': f'Opportunità «{o.titolo}» creata.'}


@tool('sposta_opportunita', 'Cambia stadio opportunità',
      "Sposta un'opportunità in un altro stadio della pipeline (anche chiusa vinta o persa).",
      area='crm', perm='opportunita.modifica', lettura=False,
      proprieta={'opportunita': {'type': 'string', 'description': 'Titolo (anche parziale) o id.'},
                 'cliente': CLIENTE, 'stadio': {'type': 'string', 'enum': STADI}},
      obbligatori=('opportunita', 'stadio'))
def _sposta_opportunita(a, user):
    from opportunita.models import Opportunita

    stadio = a.get('stadio')
    if stadio not in STADI:
        raise DatiNonValidi({'stadio': f'Stadio sconosciuto: {stadio}.'})
    qs = Opportunita.objects.select_related('cliente')
    pk = servizi._per_id(a.get('opportunita'))
    if pk is not None:
        qs = qs.filter(pk=pk)
    else:
        qs = qs.filter(titolo__icontains=str(a.get('opportunita') or '').strip())
    if a.get('cliente') not in (None, ''):
        qs = qs.filter(cliente=_cliente(a['cliente']))
    o = _unico(qs, 'opportunita', a.get('opportunita'), 'opportunità',
               lambda x: f'{x.titolo} — {x.cliente}')
    prima = o.get_stadio_display()
    o.stadio = stadio
    o.ingresso_stadio = now()
    o.save(update_fields=['stadio', 'ingresso_stadio', 'updated_at'])
    return {'id': o.pk, 'titolo': o.titolo, 'cliente': o.cliente.ragione_sociale,
            'stadio': o.get_stadio_display(), 'url': _url('opportunita:detail', o.pk),
            'messaggio': f'«{o.titolo}»: da {prima} a {o.get_stadio_display()}.'}


# --------------------------------------------------------------------------- #
# Scrittura: cashflow
# --------------------------------------------------------------------------- #

@tool('crea_scadenza_fiscale', 'Crea scadenza fiscale',
      'Una scadenza fiscale da ricordare nel cashflow (IVA, F24, INPS, ritenute…).',
      area='cashflow', perm='cashflow.modifica', lettura=False,
      proprieta={'data_scadenza': DATA, 'tipo': {'type': 'string', 'enum': ['iva', 'f24', 'inps', 'inail',
                 'ires', 'irap', 'enasarco', 'ritenute', 'altro']}, 'descrizione': S,
                 'importo': {'type': 'number'}, 'note': S},
      obbligatori=('data_scadenza', 'descrizione', 'importo'))
def _crea_scadenza_fiscale(a, user):
    from cashflow.models import ScadenzaFiscale, TipoScadenzaFiscale

    errori = {}
    importo = servizi._decimale(a.get('importo'), 'importo', errori)
    tipo = a.get('tipo') or TipoScadenzaFiscale.ALTRO
    if tipo not in TipoScadenzaFiscale.values:
        errori['tipo'] = f'Tipo sconosciuto: {tipo}.'
    descrizione = str(a.get('descrizione') or '').strip()[:200]
    if not descrizione:
        errori['descrizione'] = 'Obbligatoria.'
    giorno = _giorno(a.get('data_scadenza'), 'data_scadenza')
    if giorno is None:
        errori['data_scadenza'] = 'Obbligatoria.'
    if errori:
        raise DatiNonValidi(errori)
    s = ScadenzaFiscale.objects.create(data_scadenza=giorno, tipo=tipo, descrizione=descrizione,
                                       importo=importo, note=str(a.get('note') or ''))
    return {'id': s.pk, 'data_scadenza': s.data_scadenza.isoformat(), 'tipo': s.get_tipo_display(),
            'descrizione': s.descrizione, 'importo': _d(s.importo),
            'messaggio': f'Scadenza «{s}» creata.'}


@tool('segna_scadenza_fiscale_pagata', 'Segna scadenza fiscale pagata',
      'Registra il pagamento di una scadenza fiscale aperta, indicata per data (e tipo o descrizione).',
      area='cashflow', perm='cashflow.modifica', lettura=False,
      proprieta={'data_scadenza': DATA, 'tipo': S, 'descrizione': S, 'data_pagamento': DATA},
      obbligatori=('data_scadenza',))
def _paga_scadenza_fiscale(a, user):
    from cashflow.models import ScadenzaFiscale

    qs = ScadenzaFiscale.objects.filter(pagata=False,
                                        data_scadenza=_giorno(a.get('data_scadenza'), 'data_scadenza'))
    if a.get('tipo'):
        qs = qs.filter(tipo=str(a['tipo']).lower())
    if a.get('descrizione'):
        qs = qs.filter(descrizione__icontains=str(a['descrizione']).strip())
    s = _unico(qs, 'data_scadenza', a.get('data_scadenza'), 'scadenza fiscale aperta')
    s.pagata = True
    s.data_pagamento = _giorno(a.get('data_pagamento'), 'data_pagamento', predefinito=localdate())
    s.save(update_fields=['pagata', 'data_pagamento', 'updated_at'])
    return {'id': s.pk, 'descrizione': str(s), 'importo': _d(s.importo),
            'data_pagamento': s.data_pagamento.isoformat(), 'messaggio': f'«{s}» segnata pagata.'}


@tool('crea_spesa_ricorrente', 'Crea spesa ricorrente',
      "Una spesa che si ripete (affitto, leasing, abbonamenti): genera le scadenze dei prossimi "
      '12 mesi nel cashflow.',
      area='cashflow', perm='cashflow.modifica', lettura=False,
      proprieta={'descrizione': S, 'importo': {'type': 'number'},
                 'periodicita': {'type': 'string', 'enum': ['mensile', 'bimestrale', 'trimestrale',
                                                            'quadrimestrale', 'semestrale', 'annuale']},
                 'giorno_del_mese': {'type': 'integer'}, 'data_inizio': DATA, 'fornitore': S, 'note': S},
      obbligatori=('descrizione', 'importo'))
def _crea_spesa(a, user):
    from cashflow.models import Periodicita, SpesaRicorrente

    errori = {}
    importo = servizi._decimale(a.get('importo'), 'importo', errori)
    periodicita = a.get('periodicita') or Periodicita.MENSILE
    if periodicita not in Periodicita.values:
        errori['periodicita'] = f'Periodicità sconosciuta: {periodicita}.'
    descrizione = str(a.get('descrizione') or '').strip()[:200]
    if not descrizione:
        errori['descrizione'] = 'Obbligatoria.'
    if errori:
        raise DatiNonValidi(errori)
    inizio = _giorno(a.get('data_inizio'), 'data_inizio', predefinito=localdate())
    spesa = SpesaRicorrente.objects.create(
        descrizione=descrizione, importo=importo, periodicita=periodicita,
        giorno_del_mese=_intero(a.get('giorno_del_mese'), 'giorno_del_mese', predefinito=inizio.day,
                                minimo=1, massimo=31),
        data_inizio=inizio, note=str(a.get('note') or ''),
        fornitore=_fornitore(a['fornitore']) if a.get('fornitore') else None)
    nuove = spesa.genera_scadenze(inizio + timedelta(days=365))
    return {'id': spesa.pk, 'descrizione': spesa.descrizione, 'importo': _d(spesa.importo),
            'periodicita': spesa.get_periodicita_display(), 'scadenze_generate': nuove,
            'url': _url('cashflow:spesa_ricorrente_detail', spesa.pk),
            'messaggio': f'Spesa «{spesa.descrizione}» creata con {nuove} scadenze.'}


# --------------------------------------------------------------------------- #
# Rimborsi chilometrici
# --------------------------------------------------------------------------- #

STATI_RIMBORSO = ['bozza', 'approvato', 'pagato']


def _rimborso(r) -> dict:
    return {'id': r.pk, 'data': r.data.isoformat(),
            'amministratore': r.amministratore.get_full_name() or r.amministratore.get_username(),
            'riepilogo_mensile': r.is_riepilogo_mensile,
            'tratta': 'Riepilogo mensile' if r.is_riepilogo_mensile
            else f'{r.partenza} → {r.destinazione}',
            'km': _d(r.km), 'tariffa_km': str(r.tariffa_km) if r.tariffa_km is not None else None,
            'importo': _d(r.importo), 'causale': r.causale,
            'cliente': r.cliente.ragione_sociale if r.cliente_id else None,
            'stato': r.get_stato_display(),
            'data_pagamento': r.data_pagamento.isoformat() if r.data_pagamento else None,
            'url': _url('cashflow:rimborso_km_update', r.pk)}


@tool('elenco_rimborsi_km', 'Rimborsi chilometrici',
      'Le trasferte e i riepiloghi mensili dei rimborsi km, per stato, periodo, cliente o '
      'tratta, con i totali da approvare e da pagare.',
      area='cashflow', perm='cashflow.vedi',
      proprieta={'stato': {'type': 'string', 'enum': STATI_RIMBORSO}, 'da': DATA, 'a': DATA,
                 'cliente': CLIENTE,
                 'testo': {'type': 'string', 'description': 'Cerca in partenza, destinazione e causale.'},
                 'solo_miei': {'type': 'boolean', 'description': "Solo i rimborsi dell'utente collegato."}})
def _elenco_rimborsi(a, user):
    from cashflow.models import RimborsoChilometrico

    qs = RimborsoChilometrico.objects.select_related('amministratore', 'cliente')
    if a.get('stato') not in (None, ''):
        if a['stato'] not in STATI_RIMBORSO:
            raise DatiNonValidi({'stato': f'Stato sconosciuto: {a["stato"]}.'})
        qs = qs.filter(stato=a['stato'])
    da, al = _giorno(a.get('da'), 'da'), _giorno(a.get('a'), 'a')
    if da:
        qs = qs.filter(data__gte=da)
    if al:
        qs = qs.filter(data__lte=al)
    if a.get('cliente') not in (None, ''):
        qs = qs.filter(cliente=_cliente(a['cliente']))
    testo = str(a.get('testo') or '').strip()
    if testo:
        qs = qs.filter(Q(partenza__icontains=testo) | Q(destinazione__icontains=testo)
                       | Q(causale__icontains=testo))
    if a.get('solo_miei') is True:
        qs = qs.filter(amministratore=user)
    tot = qs.aggregate(km_totali=Sum('km'), importo_totale=Sum('importo'),
                       da_approvare=Sum('importo', filter=Q(stato='bozza')),
                       da_pagare=Sum('importo', filter=~Q(stato='pagato')))
    elementi = [_rimborso(r) for r in qs[:300]]
    return tabella('Rimborsi chilometrici', [('data', 'Data'), ('amministratore', 'Chi'),
                   ('tratta', 'Tratta'), ('km', 'Km'), ('importo', 'Importo €'), ('stato', 'Stato')],
                   elementi, totali={k: _d(v or Decimal('0')) for k, v in tot.items()})


@tool('crea_rimborso_km', 'Crea rimborso chilometrico',
      "Un rimborso chilometrico dell'utente collegato, in bozza. Trasferta singola: importo = km × "
      'tariffa (con andata_e_ritorno i km si raddoppiano; senza tariffa_km vale l\'ultima usata '
      "dall'utente). Riepilogo mensile: riepilogo_mensile=true e l'importo totale del mese.",
      area='cashflow', perm='cashflow.modifica', lettura=False,
      proprieta={'data': {'type': 'string', 'description': 'Data della trasferta (o un giorno del '
                                                           'mese per il riepilogo), AAAA-MM-GG; oggi se manca.'},
                 'partenza': S, 'destinazione': S,
                 'km': {'type': 'number', 'description': 'Km percorsi (di sola andata se andata_e_ritorno).'},
                 'andata_e_ritorno': {'type': 'boolean', 'description': 'true = i km valgono doppi.'},
                 'tariffa_km': {'type': 'number', 'description': '€/km, es. dalle tabelle ACI.'},
                 'riepilogo_mensile': {'type': 'boolean',
                                       'description': 'Totale forfettario del mese, senza tratta né km.'},
                 'importo': {'type': 'number', 'description': 'Solo per il riepilogo mensile.'},
                 'causale': S, 'cliente': CLIENTE, 'note': S})
def _crea_rimborso(a, user):
    from cashflow.models import RimborsoChilometrico

    errori = {}
    riepilogo = a.get('riepilogo_mensile') is True
    giorno = _giorno(a.get('data'), 'data', predefinito=localdate())
    cliente = servizi.risolvi_cliente(a.get('cliente'), 'cliente', errori, obbligatorio=False)
    dati = {'data': giorno, 'amministratore': user, 'is_riepilogo_mensile': riepilogo,
            'causale': str(a.get('causale') or '').strip()[:200], 'cliente': cliente,
            'note': str(a.get('note') or '')}
    if riepilogo:
        dati['importo'] = servizi._decimale(a.get('importo'), 'importo', errori)
    else:
        for campo in ('partenza', 'destinazione'):
            dati[campo] = str(a.get(campo) or '').strip()[:200]
            if not dati[campo]:
                errori[campo] = 'Obbligatoria.'
        km = servizi._decimale(a.get('km'), 'km', errori)
        if km is not None and a.get('andata_e_ritorno') is True:
            km *= 2
        if km is not None and km >= 1000000:
            errori['km'] = 'Troppi km.'
        dati['km'] = km
        if a.get('tariffa_km') in (None, ''):
            ultimo = (RimborsoChilometrico.objects.filter(amministratore=user, tariffa_km__gt=0)
                      .order_by('-data', '-pk').first())
            if ultimo is None:
                errori['tariffa_km'] = 'Obbligatoria: nessuna tariffa usata in precedenza.'
            else:
                dati['tariffa_km'] = ultimo.tariffa_km
        else:
            try:
                tariffa = Decimal(str(a['tariffa_km']).strip()).quantize(Decimal('0.0001'))
            except (ArithmeticError, ValueError, TypeError):
                errori['tariffa_km'] = 'Non è un numero.'
            else:
                if not tariffa.is_finite() or tariffa <= 0 or tariffa >= 100:
                    errori['tariffa_km'] = 'Deve essere maggiore di zero (€/km).'
                dati['tariffa_km'] = tariffa
    if errori:
        raise DatiNonValidi(errori)
    r = RimborsoChilometrico.objects.create(**dati)
    r = RimborsoChilometrico.objects.select_related('amministratore', 'cliente').get(pk=r.pk)
    cosa = f'riepilogo di {r.data:%m/%Y}' if riepilogo else f'{r.partenza} → {r.destinazione}, {_d(r.km)} km'
    return {**_rimborso(r), 'messaggio': f'Rimborso di {r.importo} € registrato ({cosa}).'}


@tool('cambia_stato_rimborso_km', 'Approva o paga rimborso km',
      'Porta un rimborso chilometrico (per id, vedi elenco_rimborsi_km) in approvato o pagato, o '
      'lo riporta indietro. Solo un Amministratore (anche sui propri rimborsi); una bozza va '
      'approvata prima di essere pagata.',
      area='cashflow', perm='cashflow.modifica', lettura=False,
      proprieta={'id': {'type': 'integer', 'description': 'Id del rimborso.'},
                 'stato': {'type': 'string', 'enum': STATI_RIMBORSO},
                 'data_pagamento': {'type': 'string', 'description': 'Per «pagato», AAAA-MM-GG; oggi se manca.'}},
      obbligatori=('id', 'stato'))
def _stato_rimborso(a, user):
    from cashflow.models import RimborsoChilometrico, StatoRimborso, puo_approvare_rimborsi

    if not puo_approvare_rimborsi(user):
        raise DatiNonValidi({'stato': 'Solo un amministratore può approvare o pagare i rimborsi.'})
    stato = a.get('stato')
    if stato not in STATI_RIMBORSO:
        raise DatiNonValidi({'stato': f'Stato sconosciuto: {stato}.'})
    pk = _intero(a.get('id'), 'id')
    r = RimborsoChilometrico.objects.select_related('amministratore', 'cliente').filter(pk=pk).first()
    if r is None:
        raise DatiNonValidi({'id': f'Nessun rimborso con id {pk}.'})
    if stato == StatoRimborso.PAGATO and r.stato == StatoRimborso.BOZZA:
        raise DatiNonValidi({'stato': 'Il rimborso è in bozza: va approvato prima di essere pagato.'})
    prima = r.get_stato_display()
    r.stato = stato
    r.data_pagamento = (_giorno(a.get('data_pagamento'), 'data_pagamento', predefinito=localdate())
                        if stato == StatoRimborso.PAGATO else None)
    r.save()
    return {**_rimborso(r), 'messaggio': f'Rimborso {r.pk} ({r.importo} €): da {prima} a '
                                         f'{r.get_stato_display()}.'}
