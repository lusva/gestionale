"""API v2: lettura e scrittura, pensata per NIKI (la chat) e integrazioni.

Rispetto alla v1 (sola lettura): il token va solo nell'header, le scritture
vogliono un token con ``scrittura`` attiva e il permesso del ruolo, e ogni
POST accetta ``?anteprima=1`` (valida e calcola, non salva) e l'header
``Idempotency-Key`` (la stessa chiave ripetuta restituisce la stessa
risposta invece di creare un secondo documento).

    GET  /api/v2/health
    GET  /api/v2/clienti?q=               clienti con forma di pagamento e IVA predefinite
    POST /api/v2/clienti[?anteprima=1]    crea un cliente (stesse regole del form)
    GET  /api/v2/clienti/<id>
    GET  /api/v2/posizioni-iva
    GET  /api/v2/forme-pagamento
    GET  /api/v2/conti-correnti           i conti dell'azienda
    GET  /api/v2/fatture?cliente=&q=&pagata=&anno=&numero=&da=&a=
    POST /api/v2/fatture[?anteprima=1]    crea una fattura (vedi documenti/servizi.py)
    GET  /api/v2/fatture/<id>

Errori: ``{"errore": "<codice>", ...}``; dati non validi: 400
``{"errore": "dati_non_validi", "errori": {"righe.0.iva_id": "…"}}``.
Invio allo SDI, invio email ed eliminazioni restano fuori di proposito.
"""
from __future__ import annotations

import json
from datetime import date

from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from django.views.decorators.csrf import csrf_exempt

from accounts.permissions import has_perm
from anagrafiche.models import ContoCorrente, FormePagamento, PosizioneIva
from clienti.models import Cliente
from documenti import servizi
from documenti.models import TestataFattura

from .auth import token_v2
from .models import RichiestaIdempotente, hash_corpo
from .serializers import (
    cliente_v2,
    conto_corrente_to_dict,
    fattura_v2,
    forma_pagamento_to_dict,
    posizione_iva_to_dict,
    riga_to_dict,
    url_assoluto,
    valori_to_dict,
)
from .views import _paginate


def _metodo_non_ammesso():
    return JsonResponse({'errore': 'metodo_non_ammesso'}, status=405)


def _pagina(request, qs, serializza):
    page_obj, paginator = _paginate(request, qs)
    return JsonResponse({
        'results': [serializza(o) for o in page_obj.object_list],
        'count': paginator.count,
        'page': page_obj.number,
        'next_page': page_obj.next_page_number() if page_obj.has_next() else None,
    })


def _corpo_json(request):
    try:
        return json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        return None


def _data_param(request, nome):
    valore = (request.GET.get(nome) or '').strip()
    if not valore:
        return None
    try:
        return date.fromisoformat(valore)
    except ValueError:
        return False


def _con_idempotenza(request, esegui):
    """Esegue ``esegui() -> (stato_http, corpo)`` una volta sola per
    ``Idempotency-Key``. Le risposte riuscite si ricordano (nella stessa
    transazione del documento); gli errori no, così si può correggere e
    riprovare con la stessa chiave. La stessa chiave con un corpo diverso è
    un errore (422); le chiavi scadono dopo ``IDEMPOTENZA_ORE``."""
    chiave = request.META.get('HTTP_IDEMPOTENCY_KEY', '').strip()
    if not chiave:
        stato, corpo = esegui()
        return JsonResponse(corpo, status=stato)
    if len(chiave) > 80:
        return JsonResponse({'errore': 'chiave_troppo_lunga'}, status=400)

    impronta = hash_corpo(request.body or b'')

    def ripeti(prec):
        if prec.percorso != request.path:
            return JsonResponse({'errore': 'chiave_gia_usata',
                                 'dettaglio': f'Usata per {prec.percorso}.'}, status=409)
        if prec.hash_corpo and prec.hash_corpo != impronta:
            return JsonResponse({'errore': 'chiave_gia_usata',
                                 'dettaglio': 'La chiave è già stata usata con dati diversi.'},
                                status=422)
        risposta = JsonResponse(prec.risposta, status=prec.stato_http)
        risposta['Idempotent-Replayed'] = 'true'
        return risposta

    RichiestaIdempotente.pulisci(token=request.api_token)
    prec = RichiestaIdempotente.objects.filter(token=request.api_token, chiave=chiave).first()
    if prec is not None:
        return ripeti(prec)
    try:
        with transaction.atomic():
            stato, corpo = esegui()
            if stato < 300:
                RichiestaIdempotente.objects.create(
                    token=request.api_token, chiave=chiave, percorso=request.path,
                    hash_corpo=impronta, stato_http=stato, risposta=corpo)
    except (IntegrityError, OperationalError):
        # la stessa chiave è arrivata due volte insieme: vince la prima
        # (OperationalError: su SQLite la seconda trova il DB bloccato)
        prec = RichiestaIdempotente.objects.filter(token=request.api_token, chiave=chiave).first()
        if prec is None:
            return JsonResponse({'errore': 'richiesta_concorrente',
                                 'dettaglio': 'Una richiesta con la stessa chiave è in corso: '
                                              'riprova tra poco.'}, status=409)
        return ripeti(prec)
    return JsonResponse(corpo, status=stato)


# ---------------------------------------------------------------------------
# Lettura
# ---------------------------------------------------------------------------

@token_v2()
def health(request):
    if request.method != 'GET':
        return _metodo_non_ammesso()
    tok = request.api_token
    return JsonResponse({
        'status': 'ok', 'version': 'v2', 'utente': tok.user.email or tok.user.get_username(),
        'scrittura': tok.scrittura,
        'permessi': {p: has_perm(tok.user, p) for p in (
            'clienti.vedi', 'documenti.vedi', 'documenti.modifica')},
    })


# Campi di un cliente nuovo accettati dall'API: quelli del form «Nuovo cliente»
# (clienti/forms.py), più il referente. Il resto (settore, tag, account
# manager) si sistema dal gestionale.
CAMPI_CLIENTE = ('ragione_sociale', 'tipo', 'partita_iva', 'codice_fiscale', 'codice_sdi', 'pec',
                 'indirizzo', 'cap', 'citta', 'provincia', 'nazione', 'stato', 'note',
                 'ref_nome', 'ref_cognome', 'ref_email', 'ref_tel')


def _form_cliente(dati):
    """Il form del gestionale compilato con i dati dell'API: stesse regole
    (P.IVA, CAP, doppioni) del cliente creato a mano."""
    from clienti.forms import ClienteForm
    from clienti.models import StatoCliente, TipoCliente

    if not isinstance(dati, dict):
        return None
    valori = {k: str(dati.get(k) or '').strip() for k in CAMPI_CLIENTE}
    valori['tipo'] = valori['tipo'] or TipoCliente.AZIENDA
    valori['stato'] = valori['stato'] or StatoCliente.ATTIVO
    valori['nazione'] = (valori['nazione'] or 'IT').upper()
    return ClienteForm(data=valori)


def _avvisi_cliente(form) -> list[str]:
    """Controlli non bloccanti, come nel form: checksum della P.IVA."""
    from clienti.forms import stdnum_iva

    avvisi = []
    piva = form.cleaned_data.get('partita_iva', '')
    if stdnum_iva is not None and piva and not stdnum_iva.is_valid(piva[2:]):
        avvisi.append('La partita IVA non supera il controllo della cifra finale: ricontrollala.')
    if not form.cleaned_data.get('codice_sdi') and not form.cleaned_data.get('pec'):
        avvisi.append('Senza codice SDI né PEC le fatture elettroniche vanno al codice 0000000.')
    return avvisi


def _errori_form(form) -> dict:
    return {k: ' '.join(str(m) for m in v) for k, v in form.errors.items()}


def _valida_cliente(form) -> dict:
    """Gli errori del form, più il doppione: la P.IVA può essere salvata con
    o senza «IT» davanti, e il vincolo unique del modello ne vede una sola."""
    if not form.is_valid():
        return _errori_form(form)
    piva = form.cleaned_data['partita_iva']
    doppio = Cliente.objects.filter(partita_iva__in=(piva, piva[2:])).first()
    if doppio is not None:
        return {'partita_iva': f'Esiste già un cliente con questa partita IVA: '
                               f'{doppio.ragione_sociale} (id {doppio.pk}).'}
    return {}


@token_v2(perm='clienti.vedi')
def _clienti_anteprima(request):
    form = _form_cliente(_corpo_json(request))
    if form is None:
        return JsonResponse({'errore': 'json_non_valido'}, status=400)
    errori = _valida_cliente(form)
    if errori:
        return JsonResponse({'errore': 'dati_non_validi', 'errori': errori}, status=400)
    return JsonResponse({'anteprima': True, 'avvisi': _avvisi_cliente(form),
                         'dati': {k: form.cleaned_data.get(k, '') for k in CAMPI_CLIENTE}})


@token_v2(scrittura=True, perm='clienti.modifica')
def _clienti_crea(request):
    form = _form_cliente(_corpo_json(request))
    if form is None:
        return JsonResponse({'errore': 'json_non_valido'}, status=400)

    def esegui():
        errori = _valida_cliente(form)
        if errori:
            return 400, {'errore': 'dati_non_validi', 'errori': errori}
        c = form.save()
        c = Cliente.objects.select_related('settore', 'forme_pagamento', 'posizione_iva').get(pk=c.pk)
        return 201, {**cliente_v2(c), 'url': url_assoluto(c.get_absolute_url())}

    return _con_idempotenza(request, esegui)


@csrf_exempt
def clienti(request):
    if request.method == 'GET':
        return _clienti_elenco(request)
    if request.method == 'POST':
        if request.GET.get('anteprima') == '1':
            return _clienti_anteprima(request)
        return _clienti_crea(request)
    return _metodo_non_ammesso()


@token_v2(perm='clienti.vedi')
def _clienti_elenco(request):
    qs = Cliente.objects.select_related(
        'settore', 'forme_pagamento', 'posizione_iva').order_by('ragione_sociale')
    q = (request.GET.get('q') or '').strip()
    if q:
        # ogni parola deve comparire nella ragione sociale, oppure è una P.IVA / CF
        per_nome = Q()
        for parola in q.split()[:6]:
            per_nome &= Q(ragione_sociale__icontains=parola)
        qs = qs.filter(per_nome | Q(partita_iva__iexact=q) | Q(codice_fiscale__iexact=q))
    return _pagina(request, qs, cliente_v2)


@token_v2(perm='clienti.vedi')
def cliente(request, pk):
    if request.method != 'GET':
        return _metodo_non_ammesso()
    c = get_object_or_404(
        Cliente.objects.select_related('settore', 'forme_pagamento', 'posizione_iva'), pk=pk)
    return JsonResponse(cliente_v2(c))


@token_v2(perm='documenti.vedi')
def posizioni_iva(request):
    if request.method != 'GET':
        return _metodo_non_ammesso()
    return JsonResponse({'results': [posizione_iva_to_dict(p) for p in PosizioneIva.objects.all()]})


@token_v2(perm='documenti.vedi')
def forme_pagamento(request):
    if request.method != 'GET':
        return _metodo_non_ammesso()
    qs = FormePagamento.objects.prefetch_related('scadenze').order_by('pk')
    return JsonResponse({'results': [forma_pagamento_to_dict(f) for f in qs]})


@token_v2(perm='documenti.vedi')
def conti_correnti(request):
    if request.method != 'GET':
        return _metodo_non_ammesso()
    qs = ContoCorrente.objects.filter(anagrafica_azienda__isnull=False).order_by('pk')
    return JsonResponse({'results': [conto_corrente_to_dict(c) for c in qs]})


# ---------------------------------------------------------------------------
# Fatture
# ---------------------------------------------------------------------------

@token_v2(perm='documenti.vedi')
def _fatture_elenco(request):
    qs = TestataFattura.objects.select_related('cliente').order_by('-anno', '-numero')
    g = request.GET
    if g.get('cliente'):
        qs = qs.filter(cliente_id=g['cliente']) if g['cliente'].isdigit() else qs.none()
    if g.get('q'):
        qs = qs.filter(cliente__ragione_sociale__icontains=g['q'].strip())
    if g.get('pagata') in ('0', '1'):
        qs = qs.filter(pagata=g['pagata'] == '1')
    for nome in ('anno', 'numero'):
        if g.get(nome):
            qs = qs.filter(**{nome: g[nome]}) if g[nome].isdigit() else qs.none()
    if 'sezionale' in g:
        qs = qs.filter(sezionale=g['sezionale'])
    da, a = _data_param(request, 'da'), _data_param(request, 'a')
    if da is False or a is False:
        return JsonResponse({'errore': 'data_non_valida'}, status=400)
    if da:
        qs = qs.filter(data_documento__gte=da)
    if a:
        qs = qs.filter(data_documento__lte=a)
    return _pagina(request, qs, fattura_v2)


def _anteprima(dati):
    try:
        p = servizi.anteprima_fattura(dati)
    except servizi.DatiNonValidi as e:
        return JsonResponse({'errore': 'dati_non_validi', 'errori': e.errori}, status=400)
    t = p.testata
    numero = servizi.numero_previsto(t.sezionale)
    return JsonResponse({
        'anteprima': True,
        'numero_previsto': numero,
        # l'anno è quello di adesso, come in TestataFattura.save
        'etichetta_prevista': f'{t.sezionale + "/" if t.sezionale else ""}{numero}/{now().year}',
        'cliente': cliente_v2(t.cliente),
        'tipo_documento': t.tipo_documento,
        'data_documento': t.data_documento.isoformat(),
        'forma_pagamento': ({'id': t.forme_pagamento.pk, 'nome': str(t.forme_pagamento)}
                            if t.forme_pagamento else None),
        'righe': [riga_to_dict(r) for r in p.righe],
        'valori': valori_to_dict(p.valori),
        'scadenze': [{'data': s['data'].isoformat(), 'importo': str(s['importo'])}
                     for s in servizi.scadenze_previste(p)],
    })


@token_v2(perm='documenti.vedi')
def _fatture_anteprima(request):
    dati = _corpo_json(request)
    if dati is None:
        return JsonResponse({'errore': 'json_non_valido'}, status=400)
    return _anteprima(dati)


@token_v2(scrittura=True, perm='documenti.modifica')
def _fatture_crea(request):
    dati = _corpo_json(request)
    if dati is None:
        return JsonResponse({'errore': 'json_non_valido'}, status=400)

    def esegui():
        try:
            f = servizi.crea_fattura(dati)
        except servizi.DatiNonValidi as e:
            return 400, {'errore': 'dati_non_validi', 'errori': e.errori}
        return 201, fattura_v2(f, dettaglio=True)

    return _con_idempotenza(request, esegui)


@csrf_exempt
def fatture(request):
    if request.method == 'GET':
        return _fatture_elenco(request)
    if request.method == 'POST':
        if request.GET.get('anteprima') == '1':
            return _fatture_anteprima(request)
        return _fatture_crea(request)
    return _metodo_non_ammesso()


@token_v2(perm='documenti.vedi')
def fattura(request, pk):
    if request.method != 'GET':
        return _metodo_non_ammesso()
    f = get_object_or_404(TestataFattura.objects.select_related('cliente', 'forme_pagamento'), pk=pk)
    return JsonResponse(fattura_v2(f, dettaglio=True))
