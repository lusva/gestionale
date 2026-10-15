"""Server MCP del gestionale (Model Context Protocol), su ``/mcp``.

Trasporto «streamable HTTP» senza stato: ogni POST porta un messaggio
JSON-RPC 2.0 (o un elenco) e riceve la risposta in JSON; niente stream SSE
(GET risponde 405, come prevede il protocollo per chi non ne offre). Metodi:
``initialize``, ``ping``, ``tools/list``, ``tools/call``.

Autenticazione: lo stesso token Bearer dell'API (Impostazioni › API).
``tools/list`` mostra solo i tool che il ruolo dell'utente può usare, e
quelli che scrivono solo se il token ha «Può scrivere».

Scritture sicure:
- ``anteprima: true`` esegue l'operazione dentro una transazione e poi la
  annulla (numerazione, magazzino, audit compresi; webhook sospesi): chi
  chiama vede esattamente il risultato, senza che resti nulla;
- ``chiave`` fa da Idempotency-Key: la stessa chiave ripetuta restituisce il
  risultato di allora invece di creare un secondo documento.

La conferma di chi usa il tool (il modulo di NIKI, l'approvazione di Claude
Desktop) sta nel client: qui c'è il minimo indispensabile perché un errore
non diventi un documento doppio.
"""
from __future__ import annotations

import json
import logging

from django.db import IntegrityError, OperationalError, transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.permissions import has_perm
from documenti.servizi import DatiNonValidi

from . import webhooks
from .auth import azienda_token_valida, estrai_token, richiesta_token
from .mcp_tools import REGISTRO
from .models import ApiToken, RichiestaIdempotente, hash_corpo

log = logging.getLogger(__name__)

PROTOCOLLI = ('2025-06-18', '2025-03-26', '2024-11-05')
ISTRUZIONI = (
    "Gestionale AigisLab: clienti, documenti di vendita (offerte, ordini, DDT, fatture, note di "
    "credito), incassi, acquisti, magazzino, CRM, cashflow e rimborsi chilometrici. Clienti, "
    "fornitori e documenti si indicano per nome o numero, come li direbbe una persona. Prima di "
    "ogni scrittura chiama il tool con anteprima=true, mostra il risultato all'utente e procedi "
    "solo se conferma; passa sempre una chiave univoca per ogni operazione. Le fatture non "
    "vengono inviate allo SDI.")


class _Annulla(Exception):
    def __init__(self, risultato):
        super().__init__('anteprima')
        self.risultato = risultato


def _token(request):
    tok = ApiToken.da_token(estrai_token(request))
    if tok is None or not tok.user.is_active or not azienda_token_valida(tok):
        return None
    return tok


def _visibili(tok):
    return [t for t in REGISTRO.values()
            if has_perm(tok.user, t.perm) and (t.lettura or tok.scrittura)]


def _risultato(dati: dict, errore: bool = False) -> dict:
    testo = dati.get('messaggio') or ''
    return {'content': [{'type': 'text',
                         'text': (testo + '\n' if testo else '') + json.dumps(dati, ensure_ascii=False,
                                                                              default=str)}],
            'structuredContent': dati, 'isError': errore}


def _errore_tool(codice: str, messaggio: str, errori: dict | None = None) -> dict:
    return _risultato({'errore': codice, 'messaggio': messaggio, 'errori': errori or {}}, errore=True)


def _chiama(tok, nome: str, argomenti: dict) -> dict:
    t = REGISTRO[nome]                    # il nome è già stato controllato da _rispondi
    if not has_perm(tok.user, t.perm):
        return _errore_tool('permesso_negato', f"L'utente non ha il permesso {t.perm}.")
    if not t.lettura and not tok.scrittura:
        return _errore_tool('token_sola_lettura',
                            'Il token è di sola lettura: nel gestionale, Impostazioni › API, creane '
                            'uno con «Può scrivere».')
    a = dict(argomenti or {})
    anteprima = bool(a.pop('anteprima', False))
    chiave = str(a.pop('chiave', '') or '').strip()[:80]
    try:
        if t.lettura:
            return _risultato(t.esegui(a, tok.user))
        if anteprima:
            with webhooks.sospesi():
                try:
                    with transaction.atomic():
                        raise _Annulla(t.esegui(a, tok.user))
                except _Annulla as fatto:
                    out = fatto.risultato
            out = {**out, 'anteprima': True}
            if out.get('messaggio'):
                out['messaggio'] = 'Anteprima, niente salvato: ' + out['messaggio']
            return _risultato(out)
        if not chiave:
            with transaction.atomic():
                return _risultato(t.esegui(a, tok.user))
        percorso = f'mcp:{nome}'
        impronta = hash_corpo(a)
        RichiestaIdempotente.pulisci(token=tok)
        prec = RichiestaIdempotente.objects.filter(token=tok, chiave=chiave).first()
        if prec is None:
            try:
                with transaction.atomic():
                    out = t.esegui(a, tok.user)
                    RichiestaIdempotente.objects.create(token=tok, chiave=chiave, percorso=percorso,
                                                        hash_corpo=impronta, stato_http=200,
                                                        risposta=out)
                return _risultato(out)
            except (IntegrityError, OperationalError):
                # la stessa chiave è arrivata due volte insieme: vince la prima
                prec = RichiestaIdempotente.objects.filter(token=tok, chiave=chiave).first()
                if prec is None:
                    return _errore_tool('richiesta_concorrente',
                                        'Una richiesta con la stessa chiave è in corso: riprova tra poco.')
        if prec.percorso != percorso:
            return _errore_tool('chiave_gia_usata', f'La chiave è già stata usata per {prec.percorso}.')
        if prec.hash_corpo and prec.hash_corpo != impronta:
            return _errore_tool('chiave_gia_usata',
                                'La chiave è già stata usata con dati diversi: usa una chiave nuova '
                                'per ogni operazione.')
        return _risultato({**prec.risposta, 'ripetuta': True})
    except DatiNonValidi as e:
        return _errore_tool('dati_non_validi', 'Dati non validi: ' + '; '.join(
            f'{k}: {v}' for k, v in e.errori.items()), e.errori)


def _rispondi(tok, msg):
    """La risposta a un messaggio JSON-RPC, o None per una notifica."""
    if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or 'method' not in msg:
        return {'jsonrpc': '2.0', 'id': msg.get('id') if isinstance(msg, dict) else None,
                'error': {'code': -32600, 'message': 'Richiesta non valida'}}
    if 'id' not in msg:
        return None                                   # notifica (notifications/initialized…)
    mid, metodo, params = msg['id'], msg['method'], msg.get('params') or {}

    def ok(risultato):
        return {'jsonrpc': '2.0', 'id': mid, 'result': risultato}

    def errore(codice, messaggio):
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': codice, 'message': messaggio}}

    if not isinstance(params, dict):
        return errore(-32602, 'params deve essere un oggetto')

    if metodo == 'initialize':
        chiesto = params.get('protocolVersion')
        return ok({
            'protocolVersion': chiesto if chiesto in PROTOCOLLI else PROTOCOLLI[0],
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': 'gestionale-aigislab', 'title': 'Gestionale AigisLab',
                           'version': '1.0'},
            'instructions': ISTRUZIONI,
            '_meta': {'utente': tok.user.email or tok.user.get_username(),
                      'scrittura': tok.scrittura},
        })
    if metodo == 'ping':
        return ok({})
    if metodo == 'tools/list':
        return ok({'tools': [t.descrittore() for t in _visibili(tok)]})
    if metodo == 'tools/call':
        nome = params.get('name')
        argomenti = params.get('arguments') or {}
        if not isinstance(nome, str) or nome not in REGISTRO:
            return errore(-32602, f'Tool sconosciuto: {nome}')
        if not isinstance(argomenti, dict):
            return errore(-32602, 'arguments deve essere un oggetto')
        try:
            return ok(_chiama(tok, nome, argomenti))
        except Exception as e:                               # noqa: BLE001
            log.exception('tool %s non riuscito', nome)
            return ok(_errore_tool('errore_interno', f'Errore del gestionale: {e}'))
    return errore(-32601, f'Metodo sconosciuto: {metodo}')


@csrf_exempt
def mcp_view(request):
    if request.method != 'POST':
        return HttpResponse(status=405, headers={'Allow': 'POST'})
    tok = _token(request)
    if tok is None:
        return JsonResponse({'jsonrpc': '2.0', 'id': None,
                             'error': {'code': -32001, 'message': 'Token mancante o non valido'}},
                            status=401, headers={'WWW-Authenticate': 'Bearer'})
    with richiesta_token(request, tok):
        return _messaggi(request, tok)


def _messaggi(request, tok):
    """L'intero batch MCP vive nel contesto dell'azienda autenticata."""
    try:
        corpo = json.loads(request.body or b'')
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'jsonrpc': '2.0', 'id': None,
                             'error': {'code': -32700, 'message': 'JSON non valido'}}, status=400)
    if isinstance(corpo, list):
        risposte = [r for r in (_rispondi(tok, m) for m in corpo) if r is not None]
        return JsonResponse(risposte, safe=False) if risposte else HttpResponse(status=202)
    risposta = _rispondi(tok, corpo)
    return JsonResponse(risposta) if risposta is not None else HttpResponse(status=202)
