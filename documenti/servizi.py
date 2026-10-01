"""Servizi del ciclo attivo, fuori dai form HTML.

Li usano l'API v2 e il server MCP (e con loro NIKI, la chat): un documento
creato qui è identico a uno creato dal form, perché passa dagli stessi pezzi —
numerazione nel ``save`` delle testate, righe ``RigaDocumento``, imponibile
ricalcolato dai signal, movimenti di magazzino, scadenze dalla forma di
pagamento, collegamenti del workflow.

``prepara_documento`` valida i dati e costruisce gli oggetti senza salvarli;
``crea_documento`` li salva in una transazione; ``converti`` fa le
transizioni offerta → ordine → DDT / fattura → nota di credito.

Clienti, forme di pagamento, IVA e articoli si possono indicare per id o per
nome (``risolvi_*``): chi chiama da una chat scrive «Rossi», non «42». Un nome
che corrisponde a più voci è un errore che le elenca, mai una scelta a caso.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils.timezone import localdate

from anagrafiche.models import Articolo, ContoCorrente, FormePagamento, PosizioneIva
from clienti.models import Cliente

from .models import (
    RigaDocumento,
    ScadenzaFattura,
    TestataDdt,
    TestataFattura,
    TestataNotaCredito,
    TestataOfferta,
    TestataOrdine,
    _next_numero_per_anno,
)
from .utils import calcola_valori, genera_scadenze_da_forma_pagamento


# Le fatture chieste da fuori (la chat) sono immediate, salvo che il chiamante
# dica altro: il TD24 predefinito del modello è per le differite da DDT.
TIPO_PREDEFINITO = TestataFattura.TipoDocumento.TD01
MAX_RIGHE = 100
# max_digits=10, decimal_places=2 su importi e quantità
MAX_VALORE = Decimal('99999999.99')

MODELLI = {
    'offerta': TestataOfferta,
    'ordine': TestataOrdine,
    'ddt': TestataDdt,
    'fattura': TestataFattura,
    'nota_credito': TestataNotaCredito,
}
FK_RIGA = {
    'offerta': 'testata_offerta',
    'ordine': 'testata_ordine',
    'ddt': 'testata_ddt',
    'fattura': 'testata_fattura',
    'nota_credito': 'testata_nota_credito',
}
NOMI = {'offerta': 'Offerta', 'ordine': 'Ordine', 'ddt': 'DDT', 'fattura': 'Fattura',
        'nota_credito': 'Nota di credito'}
# documenti con numerazione per sezionale
CON_SEZIONALE = ('fattura', 'nota_credito')


class DatiNonValidi(Exception):
    """Dati di un documento da rifiutare: ``errori`` = {campo: messaggio}."""

    def __init__(self, errori: dict[str, str]):
        super().__init__('; '.join(f'{k}: {v}' for k, v in errori.items()))
        self.errori = errori


# ---------------------------------------------------------------------------
# Valori semplici
# ---------------------------------------------------------------------------

def _decimale(valore, campo, errori, *, predefinito=None, positivo=True):
    if valore is None or valore == '':
        if predefinito is not None:
            return predefinito
        errori[campo] = 'Obbligatorio.'
        return None
    if isinstance(valore, bool):
        errori[campo] = 'Non è un numero.'
        return None
    try:
        d = Decimal(str(valore).strip())
    except InvalidOperation:
        errori[campo] = 'Non è un numero.'
        return None
    if not d.is_finite():
        errori[campo] = 'Non è un numero.'
        return None
    d = d.quantize(Decimal('0.01'))
    if abs(d) > MAX_VALORE:
        errori[campo] = 'Valore troppo grande.'
    elif positivo and d <= 0:
        errori[campo] = 'Deve essere maggiore di zero.'
    return d


def _data(valore, campo, errori, *, predefinita=True):
    if valore is None or valore == '':
        return localdate() if predefinita else None
    try:
        return date.fromisoformat(str(valore)[:10])
    except ValueError:
        errori[campo] = 'Data non valida (AAAA-MM-GG).'
        return None


def _oggetto(model, pk, campo, errori):
    if pk is None or pk == '':
        return None
    try:
        pk = int(pk)
    except (TypeError, ValueError):
        errori[campo] = 'Id non valido.'
        return None
    obj = model.objects.filter(pk=pk).first()
    if obj is None:
        errori[campo] = f'{model._meta.verbose_name.capitalize()} {pk} inesistente.'
    return obj


def _per_id(valore) -> int | None:
    if isinstance(valore, bool):
        return None
    if isinstance(valore, int):
        return valore
    testo = str(valore or '').strip()
    return int(testo) if testo.isdigit() and len(testo) < 10 else None


# ---------------------------------------------------------------------------
# Dai nomi agli oggetti
# ---------------------------------------------------------------------------

_FORME_SOCIETARIE = {'srl', 'srls', 'spa', 'snc', 'sas', 'sapa', 'ss', 'scarl', 'coop',
                     'spett', 'spettle', 'ditta'}


def _parole(testo: str) -> list[str]:
    out = []
    for t in (testo or '').split():
        p = ''.join(c for c in t.lower() if c.isalnum())
        if p and p not in _FORME_SOCIETARIE:
            out.append(p)
    return out


def _elenco(oggetti, nome=str) -> str:
    return ', '.join(f'{nome(o)} (id {o.pk})' for o in oggetti[:6]) + ('…' if len(oggetti) > 6 else '')


def cerca_clienti(testo: str):
    """I clienti che corrispondono al testo: per P.IVA o CF esatti, altrimenti
    tutte le parole (senza forme societarie) nella ragione sociale."""
    testo = (testo or '').strip()
    qs = Cliente.objects.select_related('forme_pagamento', 'posizione_iva').order_by('ragione_sociale')
    if not testo:
        return qs
    compatto = testo.replace(' ', '').upper()
    esatti = qs.filter(Q(partita_iva__iexact=compatto) | Q(partita_iva__iexact=f'IT{compatto}')
                       | Q(codice_fiscale__iexact=compatto))
    if esatti.exists():
        return esatti
    filtro = Q()
    for p in _parole(testo)[:6]:
        filtro &= Q(ragione_sociale__icontains=p)
    return qs.filter(filtro) if filtro else qs.none()


def risolvi_cliente(valore, campo, errori, *, obbligatorio=True):
    """Il cliente indicato per id o per nome/P.IVA; errore se non c'è o se è ambiguo."""
    if valore is None or valore == '':
        if obbligatorio:
            errori[campo] = 'Obbligatorio.'
        return None
    pk = _per_id(valore)
    if pk is not None:
        return _oggetto(Cliente, pk, campo, errori)
    trovati = list(cerca_clienti(str(valore))[:20])
    if len(trovati) == 1:
        return trovati[0]
    uguali = [c for c in trovati if _parole(c.ragione_sociale) == _parole(str(valore))]
    if len(uguali) == 1:
        return uguali[0]
    if not trovati:
        errori[campo] = f'Nessun cliente corrisponde a «{valore}».'
    else:
        errori[campo] = f'Più clienti corrispondono a «{valore}»: {_elenco(trovati)}.'
    return None


def _nome_forma(f) -> str:
    return str(f)


def risolvi_forma(valore, campo, errori):
    """La forma di pagamento per id o per nome («bonifico 30»)."""
    if valore is None or valore == '':
        return None
    pk = _per_id(valore)
    if pk is not None:
        return _oggetto(FormePagamento, pk, campo, errori)
    parole = [p for p in _parole(str(valore)) if len(p) >= 3 or p.isdigit()]
    punti = []
    for f in FormePagamento.objects.all():
        nome = f'{_nome_forma(f)} {f.get_modalita_pagamento_display()}'.lower()
        punti.append((sum(p in nome for p in parole), f))
    migliori = [f for n, f in punti if n and n == max(x for x, _ in punti)]
    if len(migliori) == 1:
        return migliori[0]
    if not migliori:
        errori[campo] = f'Nessuna forma di pagamento corrisponde a «{valore}».'
    else:
        errori[campo] = (f'Più forme di pagamento corrispondono a «{valore}»: '
                         f'{_elenco(migliori, _nome_forma)}.')
    return None


def risolvi_iva(riga: dict, cliente, campo, errori, *, obbligatoria=True):
    """La posizione IVA di una riga: ``iva_id``, oppure ``aliquota_iva`` (22,
    10, 4… senza natura), oppure quella predefinita del cliente."""
    if riga.get('iva_id') not in (None, ''):
        return _oggetto(PosizioneIva, riga['iva_id'], f'{campo}.iva_id', errori)
    aliquota = riga.get('aliquota_iva')
    if aliquota not in (None, ''):
        a = _decimale(aliquota, f'{campo}.aliquota_iva', errori, positivo=False)
        if a is None:
            return None
        iva = PosizioneIva.objects.filter(aliquota=a, natura='').first()
        if iva is None:
            errori[f'{campo}.aliquota_iva'] = f'Nessuna posizione IVA al {a:g}%.'
        return iva
    if cliente is not None and cliente.posizione_iva_id:
        return cliente.posizione_iva
    if cliente is not None and obbligatoria:
        errori[f'{campo}.iva_id'] = 'Manca la posizione IVA: il cliente non ne ha una predefinita.'
    return None


def risolvi_articolo(codice, campo, errori):
    if codice is None or codice == '':
        return None
    a = Articolo.objects.filter(codice__iexact=str(codice).strip()).first()
    if a is None:
        errori[campo] = f'Nessun articolo con codice «{codice}».'
    return a


# ---------------------------------------------------------------------------
# Documenti
# ---------------------------------------------------------------------------

@dataclass
class DocumentoPreparato:
    tipo: str
    testata: object
    righe: list[RigaDocumento]
    valori: dict


# compatibilità con l'API v2 delle fatture
FatturaPreparata = DocumentoPreparato


def prepara_documento(tipo: str, dati) -> DocumentoPreparato:
    """Valida ``dati`` e costruisce testata e righe di un documento del ciclo
    attivo, senza salvare. Solleva ``DatiNonValidi``.

    Campi comuni: ``cliente`` (id, nome o P.IVA; anche ``cliente_id``),
    ``data_documento`` (oggi), ``pagamento`` (id o nome; anche
    ``forme_pagamento_id``; se manca, quella del cliente), ``conto_corrente_id``,
    ``note``, ``righe``: [{``descrizione``, ``quantita`` (1),
    ``importo_unitario`` o ``prezzo``, ``iva_id`` o ``aliquota_iva`` (se
    mancano, l'IVA del cliente), ``um``, ``articolo`` (codice), ``colli``}].
    Fattura e nota di credito: ``tipo_documento``, ``sezionale``. Offerta:
    ``scadenza``. DDT: ``causale_trasporto``, ``porto``, ``aspetto_beni``,
    ``vettore``, ``numero_palette``, ``peso_netto``, ``peso_lordo``; nei DDT i
    prezzi possono mancare."""
    if tipo not in MODELLI:
        raise DatiNonValidi({'tipo': f'Documento sconosciuto: {tipo}.'})
    if not isinstance(dati, dict):
        raise DatiNonValidi({'_': 'Serve un oggetto JSON.'})
    model = MODELLI[tipo]
    errori: dict[str, str] = {}

    cliente = risolvi_cliente(dati.get('cliente_id', dati.get('cliente')), 'cliente', errori)
    data_documento = _data(dati.get('data_documento'), 'data_documento', errori)
    forma = risolvi_forma(dati.get('forme_pagamento_id', dati.get('pagamento')), 'pagamento', errori)
    if forma is None and 'pagamento' not in errori and cliente is not None:
        forma = cliente.forme_pagamento
    conto = _oggetto(ContoCorrente, dati.get('conto_corrente_id'), 'conto_corrente_id', errori)
    note = str(dati.get('note') or '').strip()[:5000]

    extra = {}
    if tipo in CON_SEZIONALE:
        scelte = model.TipoDocumento
        predefinito = TIPO_PREDEFINITO if tipo == 'fattura' else scelte.TD04
        extra['tipo_documento'] = dati.get('tipo_documento') or predefinito
        if extra['tipo_documento'] not in scelte.values:
            errori['tipo_documento'] = f'Tipo documento sconosciuto: {extra["tipo_documento"]}.'
        extra['sezionale'] = str(dati.get('sezionale') or '').strip()
        if len(extra['sezionale']) > 10:
            errori['sezionale'] = 'Al massimo 10 caratteri.'
    if tipo == 'offerta':
        extra['scadenza'] = _data(dati.get('scadenza'), 'scadenza', errori, predefinita=False)
    if tipo == 'ddt':
        for campo in ('causale_trasporto', 'porto', 'aspetto_beni', 'vettore', 'cura'):
            extra[campo] = str(dati.get(campo) or '').strip()[:50]
        if dati.get('numero_palette') not in (None, ''):
            n = _decimale(dati['numero_palette'], 'numero_palette', errori, positivo=False)
            if n is not None and n < 0:
                errori['numero_palette'] = 'Non può essere negativo.'
            extra['numero_palette'] = int(n) if n is not None and n >= 0 else 0
        for campo in ('peso_netto', 'peso_lordo'):
            if dati.get(campo) not in (None, ''):
                extra[campo] = _decimale(dati[campo], campo, errori, positivo=False)

    righe: list[RigaDocumento] = []
    righe_dati = dati.get('righe')
    if not isinstance(righe_dati, list) or not righe_dati:
        errori['righe'] = 'Serve almeno una riga.'
        righe_dati = []
    elif len(righe_dati) > MAX_RIGHE:
        errori['righe'] = f'Al massimo {MAX_RIGHE} righe.'
        righe_dati = []
    for i, r in enumerate(righe_dati):
        chiave = f'righe.{i}'
        if not isinstance(r, dict):
            errori[chiave] = 'Ogni riga è un oggetto.'
            continue
        articolo = risolvi_articolo(r.get('articolo'), f'{chiave}.articolo', errori)
        descrizione = str(r.get('descrizione') or '').strip()
        if not descrizione and articolo is None:
            errori[f'{chiave}.descrizione'] = 'Obbligatoria.'
        quantita = _decimale(r.get('quantita'), f'{chiave}.quantita', errori,
                             predefinito=Decimal('1'))
        grezzo = r.get('importo_unitario', r.get('prezzo'))
        if grezzo in (None, '') and articolo is not None and articolo.prezzo_listino is not None:
            grezzo = articolo.prezzo_listino
        # negativo ammesso: una riga di sconto; il totale però deve restare positivo
        prezzo = _decimale(grezzo, f'{chiave}.importo_unitario', errori, positivo=False,
                           predefinito=Decimal('0') if tipo == 'ddt' else None)
        # nei DDT l'IVA serve solo se ci sono i prezzi
        iva = risolvi_iva(r, cliente, chiave, errori, obbligatoria=tipo != 'ddt' or bool(prezzo))
        um = r.get('um') or (articolo.um if articolo is not None and articolo.um else None)
        if um is not None and um not in RigaDocumento.UM.values:
            errori[f'{chiave}.um'] = f'Unità di misura sconosciuta: {um}.'
        colli = r.get('colli')
        righe.append(RigaDocumento(
            numero_riga=i + 1, articolo=articolo, descrizione_libera=descrizione[:1000] or None,
            quantita=quantita, importo_unitario=prezzo, iva=iva, um=um,
            colli=int(colli) if str(colli or '').isdigit() else 0,
        ))
    if errori:
        raise DatiNonValidi(errori)

    testata = model(cliente=cliente, data_documento=data_documento, forme_pagamento=forma,
                    conto_corrente=conto, note=note, **extra)
    valori = calcola_valori(testata, righe)
    if tipo != 'ddt' and valori['totale'] <= 0:
        raise DatiNonValidi({'righe': 'Il totale del documento deve essere maggiore di zero.'})
    return DocumentoPreparato(tipo, testata, righe, valori)


def prepara_fattura(dati) -> DocumentoPreparato:
    return prepara_documento('fattura', dati)


def anteprima_fattura(dati) -> DocumentoPreparato:
    """Come ``prepara_fattura``: nessuna scrittura, totali già calcolati."""
    return prepara_fattura(dati)


def numero_previsto(sezionale: str = '', tipo: str = 'fattura') -> int:
    """Il numero che avrebbe adesso il prossimo documento (del sezionale).

    Solo indicativo: nel frattempo un altro documento può prenderlo."""
    with transaction.atomic():
        return _next_numero_per_anno(MODELLI[tipo], localdate().year,
                                     sezionale=(sezionale or '') if tipo in CON_SEZIONALE else None)


def scadenze_previste(preparata: DocumentoPreparato) -> list[dict]:
    return genera_scadenze_da_forma_pagamento(preparata.testata,
                                              totale=preparata.valori['totale'])


def base_scadenze(fattura) -> tuple:
    """Ciò da cui dipendono le scadenze automatiche: forma di pagamento, data
    e totale. Si legge prima di una modifica e si passa a
    ``sincronizza_scadenze`` per capire se il piano va rifatto."""
    return (fattura.forme_pagamento_id, fattura.data_documento,
            calcola_valori(fattura)['totale'] if fattura.pk else None)


def sincronizza_scadenze(fattura, prima: tuple | None = None) -> bool:
    """Rigenera le scadenze della fattura dalla sua forma di pagamento (se
    ce l'ha e se ne risulta almeno una). True se le ha rigenerate.

    Mai su una fattura pagata o già emessa verso il SDI. Con ``prima`` (il
    ``base_scadenze`` letto prima della modifica) le rifà solo se forma di
    pagamento, data o totale sono cambiati o se non ce ne sono: scadenze
    ritoccate a mano restano come sono."""
    fattura.refresh_from_db()
    if fattura.pagata or fattura.emessa_sdi:
        return False
    if not fattura.forme_pagamento_id or not fattura.data_documento:
        return False
    if (prima is not None and fattura.scadenze.exists()
            and base_scadenze(fattura) == prima):
        return False
    auto = genera_scadenze_da_forma_pagamento(fattura)
    if not auto:
        return False
    fattura.scadenze.all().delete()
    for s in auto:
        ScadenzaFattura.objects.create(fattura=fattura, data=s['data'], importo=s['importo'])
    return True


def crea_documento(tipo: str, dati):
    """Valida e salva il documento con le righe (e, per una fattura, le
    scadenze), tutto o niente."""
    preparata = prepara_documento(tipo, dati)
    with transaction.atomic():
        doc = preparata.testata
        doc.save()
        for riga in preparata.righe:
            setattr(riga, FK_RIGA[tipo], doc)
            riga.save()
        if tipo == 'fattura':
            sincronizza_scadenze(doc)
    doc.refresh_from_db()
    return doc


def crea_fattura(dati) -> TestataFattura:
    return crea_documento('fattura', dati)


def trova_documento(tipo: str, numero, anno=None, sezionale: str = ''):
    """Il documento ``numero``/``anno`` (anno corrente se manca); DatiNonValidi se non c'è."""
    if tipo not in MODELLI:
        raise DatiNonValidi({'tipo': f'Documento sconosciuto: {tipo}.'})
    errori = {}
    n = _per_id(numero)
    a = _per_id(anno) if anno not in (None, '') else localdate().year
    if n is None:
        errori['numero'] = 'Serve il numero del documento.'
    if a is None:
        errori['anno'] = 'Anno non valido.'
    if errori:
        raise DatiNonValidi(errori)
    qs = MODELLI[tipo].objects.filter(numero=n, anno=a)
    if tipo in CON_SEZIONALE:
        qs = qs.filter(sezionale=str(sezionale or '').strip())
    doc = qs.select_related('cliente').first()
    if doc is None:
        raise DatiNonValidi({'numero': f'{NOMI[tipo]} {n}/{a} inesistente.'})
    return doc


# ---------------------------------------------------------------------------
# Workflow: offerta → ordine → DDT / fattura → nota di credito
# ---------------------------------------------------------------------------

_RIGA_FIELDS = (
    'numero_riga', 'articolo_id', 'descrizione_libera',
    'importo_unitario', 'quantita', 'iva_id', 'um', 'colli',
    'prezzo_acquisto',
)
# le date non si copiano: il documento nuovo nasce oggi
_CAMPI_COMUNI = (
    'cliente_id', 'fornitore_id',
    'spese_imballo', 'spese_trasporto', 'sconto', 'sconto_incondizionato',
    'note', 'forme_pagamento_id', 'conto_corrente_id',
)
CONVERSIONI = {
    'offerta_in_ordine': ('offerta', 'ordine'),
    'ordine_in_ddt': ('ordine', 'ddt'),
    'ordine_in_fattura': ('ordine', 'fattura'),
    'fattura_in_nota_credito': ('fattura', 'nota_credito'),
}


def copia_righe(origine, tipo_origine: str, nuova, tipo_nuovo: str) -> None:
    for r in RigaDocumento.objects.filter(**{f'{FK_RIGA[tipo_origine]}_id': origine.pk}):
        kw = {f: getattr(r, f) for f in _RIGA_FIELDS}
        kw[f'{FK_RIGA[tipo_nuovo]}_id'] = nuova.pk
        RigaDocumento.objects.create(**kw)


def _controlla_conversione(verso: str, origine) -> None:
    """DatiNonValidi se ``origine`` non si può (più) convertire: annullata,
    già convertita (offerta confermata, ordine con DDT o fattura, fattura
    con nota di credito)."""
    nome = f'{NOMI[CONVERSIONI[verso][0]]} {origine.numero}/{origine.anno}'
    if verso == 'offerta_in_ordine':
        if origine.stato == TestataOfferta.StatoOfferta.ANNULLATA:
            raise DatiNonValidi({'stato': f'{nome} è annullata.'})
        if (origine.stato == TestataOfferta.StatoOfferta.CONFERMATA
                or origine.ordine_collegato.exists()):
            raise DatiNonValidi({'stato': f'{nome} è già stata confermata in un ordine.'})
    elif verso in ('ordine_in_ddt', 'ordine_in_fattura'):
        if origine.stato == TestataOrdine.StatoOrdine.ANNULLATA:
            raise DatiNonValidi({'stato': f'{nome} è annullato.'})
        if verso == 'ordine_in_ddt' and origine.ddt_collegato.exclude(
                stato=TestataDdt.StatoDdt.ANNULLATO).exists():
            raise DatiNonValidi({'stato': f'{nome} ha già un DDT.'})
        if verso == 'ordine_in_fattura' and origine.fattura_collegata.exists():
            raise DatiNonValidi({'stato': f'{nome} è già stato fatturato.'})
    elif verso == 'fattura_in_nota_credito':
        # una nota di credito totale per fattura: una seconda la storna due volte
        if origine.nota_credito_collegata.exists():
            raise DatiNonValidi({'stato': f'{nome} ha già una nota di credito.'})


def converti(verso: str, origine):
    """Il documento nuovo generato da ``origine`` (``CONVERSIONI``), con le
    stesse righe e il collegamento del workflow. Tutto o niente.

    DatiNonValidi se l'origine è già stata convertita o è annullata: la
    riga dell'origine è bloccata (``select_for_update``) finché la
    transazione non finisce, così un doppio POST trova il collegamento
    appena creato e non genera un secondo documento."""
    if verso not in CONVERSIONI:
        raise DatiNonValidi({'verso': f'Conversione sconosciuta: {verso}.'})
    da, a = CONVERSIONI[verso]
    with transaction.atomic():
        origine = MODELLI[da].objects.select_for_update().get(pk=origine.pk)
        _controlla_conversione(verso, origine)
        nuovo = MODELLI[a](data_documento=localdate(), data_registrazione=localdate())
        for campo in _CAMPI_COMUNI:
            if hasattr(origine, campo) and hasattr(nuovo, campo):
                setattr(nuovo, campo, getattr(origine, campo))
        if a == 'ordine':
            nuovo.stato = TestataOrdine.StatoOrdine.ATTESA
        elif a == 'ddt':
            nuovo.stato = TestataDdt.StatoDdt.CONFERMATO
        elif a == 'fattura':
            nuovo.tipo_documento = TestataFattura.TipoDocumento.TD01
        elif a == 'nota_credito':
            nuovo.tipo_documento = TestataNotaCredito.TipoDocumento.TD04
        nuovo.save()                               # auto-numera
        # prima il collegamento, poi le righe: il magazzino guarda la catena
        # (una fattura da ordine con DDT non scarica di nuovo la merce)
        if verso == 'offerta_in_ordine':
            origine.ordine_collegato.add(nuovo)
            TestataOfferta.objects.filter(pk=origine.pk).update(
                stato=TestataOfferta.StatoOfferta.CONFERMATA)
        elif verso == 'ordine_in_ddt':
            origine.ddt_collegato.add(nuovo)
        elif verso == 'ordine_in_fattura':
            origine.fattura_collegata.add(nuovo)
        else:
            origine.nota_credito_collegata.add(nuovo)
        copia_righe(origine, da, nuovo, a)
        if verso == 'ordine_in_fattura':
            sincronizza_scadenze(nuovo)
    nuovo.refresh_from_db()
    return nuovo


def segna_pagata(fattura, data_pagamento=None):
    """Segna pagata la fattura (oggi, se non si dice quando)."""
    errori = {}
    giorno = _data(data_pagamento, 'data_pagamento', errori)
    if errori:
        raise DatiNonValidi(errori)
    if fattura.pagata:
        raise DatiNonValidi({'numero': f'La fattura {fattura.numero}/{fattura.anno} è già pagata '
                                       f'(il {fattura.data_pagamento:%d/%m/%Y}).'
                             if fattura.data_pagamento else 'La fattura è già pagata.'})
    fattura.pagata = True
    fattura.data_pagamento = giorno
    fattura.save(update_fields=['pagata', 'data_pagamento'])
    return fattura
