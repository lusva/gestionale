"""Delivery outbound di webhook.

`dispatch(evento, payload)` invia POST JSON a tutti i Webhook attivi che
accettano quell'evento. Firma HMAC-SHA256 se il webhook ha un secret.

- L'invio parte solo dopo il commit della transazione (``on_commit``): un
  salvataggio annullato non manda nulla, e ``payload`` può essere una
  funzione, chiamata a commit avvenuto, così il payload vede il documento
  completo (righe e totali compresi).
- Le chiamate HTTP girano in un thread in background (``WEBHOOK_IN_BACKGROUND``,
  attivo per default) con timeout di ``TIMEOUT_S`` secondi: la richiesta
  dell'utente non aspetta il server remoto. Nessuna coda né retry: per
  traffico serio usare Celery/RQ.
- Anti-SSRF: solo http/https verso indirizzi pubblici (``valida_url_webhook``,
  alla creazione e a ogni invio) e nessun redirect seguito.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Callable
from urllib.parse import urlsplit

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone
from tenancy.context import azienda_context, current_azienda_id

from .models import Webhook


logger = logging.getLogger(__name__)
TIMEOUT_S = 5


class UrlWebhookNonValida(ValueError):
    pass


def _ip_vietato(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified or not ip.is_global)


def valida_url_webhook(url: str) -> str:
    """Solleva ``UrlWebhookNonValida`` se l'URL non è http(s) o se l'host
    risolve verso un indirizzo privato, di loopback, link-local o riservato
    (rete interna del server, metadata del cloud…). Restituisce l'URL.

    ``WEBHOOK_CONSENTI_IP_PRIVATI = True`` (solo sviluppo) salta il
    controllo sugli indirizzi."""
    try:
        parti = urlsplit(url or '')
        porta = parti.port
    except ValueError:
        raise UrlWebhookNonValida('URL non valido.') from None
    if parti.scheme not in ('http', 'https'):
        raise UrlWebhookNonValida('Sono ammessi solo URL http:// o https://.')
    host = parti.hostname
    if not host:
        raise UrlWebhookNonValida("L'URL non ha un host.")
    if getattr(settings, 'WEBHOOK_CONSENTI_IP_PRIVATI', False):
        return url
    try:
        indirizzi = {info[4][0] for info in socket.getaddrinfo(
            host, porta or (443 if parti.scheme == 'https' else 80), proto=socket.IPPROTO_TCP)}
    except (socket.gaierror, UnicodeError):
        raise UrlWebhookNonValida(f"L'host «{host}» non si risolve.") from None
    for indirizzo in indirizzi:
        ip = ipaddress.ip_address(indirizzo.split('%', 1)[0])
        if _ip_vietato(ip):
            raise UrlWebhookNonValida(
                f"L'host «{host}» punta a un indirizzo non pubblico ({ip}): non ammesso.")
    return url


class _NessunRedirect(urllib.request.HTTPRedirectHandler):
    """Un redirect potrebbe portare verso la rete interna: è un errore."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f'Redirect non seguito verso {newurl}',
                                     headers, fp)


_opener = urllib.request.build_opener(_NessunRedirect)


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()


_locale = threading.local()


@contextmanager
def sospesi():
    """Nessun webhook dentro il blocco: le anteprime dell'MCP eseguono
    l'operazione e poi la annullano, e nessuno fuori deve sentirla."""
    prima = getattr(_locale, 'sospesi', False)
    _locale.sospesi = True
    try:
        yield
    finally:
        _locale.sospesi = prima


def dispatch(evento: str, payload: dict | Callable[[], dict | None]):
    """Invia `payload` a tutti i Webhook interessati a `evento`, dopo il
    commit. ``payload`` può essere una funzione senza argomenti, chiamata a
    commit avvenuto (se restituisce None l'evento si scarta)."""
    if getattr(_locale, 'sospesi', False):
        return
    azienda_id = current_azienda_id()
    if azienda_id is None:
        return
    # Il callback può partire quando la richiesta ha già lasciato il proprio
    # contesto; anche la costruzione differita del payload deve usare l'azienda.
    transaction.on_commit(lambda: _dopo_commit(evento, payload, azienda_id))


def _dopo_commit(evento: str, payload, azienda_id):
    with azienda_context(azienda_id):
        _prepara_consegna(evento, payload, azienda_id)


def _prepara_consegna(evento: str, payload, azienda_id):
    try:
        hooks = [h for h in Webhook.objects.filter(attivo=True) if h.accetta(evento)]
        if not hooks:
            return
        dati = payload() if callable(payload) else payload
    except Exception:                                   # noqa: BLE001
        logger.exception('Webhook %s: payload non costruito', evento)
        return
    if dati is None:
        return
    if getattr(settings, 'WEBHOOK_IN_BACKGROUND', True):
        threading.Thread(target=_consegna_tutti, args=(hooks, evento, dati, azienda_id),
                         name=f'webhook-{evento}', daemon=True).start()
    else:
        _consegna_tutti(hooks, evento, dati, azienda_id, chiudi_connessioni=False)


def _consegna_tutti(hooks, evento, dati, azienda_id, chiudi_connessioni=True):
    try:
        with azienda_context(azienda_id):
            for h in hooks:
                if h.azienda_id == azienda_id:
                    _deliver(h, evento, dati)
    finally:
        if chiudi_connessioni:
            close_old_connections()


def _deliver(hook: Webhook, evento: str, payload: dict):
    body = json.dumps({'evento': evento, 'data': payload, 'at': timezone.now().isoformat()},
                      default=str).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'gestionale-crm/1.0 webhook',
        'X-CRM-Event': evento,
    }
    if hook.secret:
        headers['X-CRM-Signature'] = f'sha256={_sign(hook.secret, body)}'

    status = None
    err = ''
    try:
        valida_url_webhook(hook.url)
        req = urllib.request.Request(hook.url, data=body, headers=headers, method='POST')
        with _opener.open(req, timeout=TIMEOUT_S) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        err = str(exc)[:500]
    except Exception as exc:
        err = str(exc)[:500]
    finally:
        Webhook.objects.filter(pk=hook.pk).update(
            ultimo_tentativo_at=timezone.now(),
            ultimo_status=status or 0,
            ultimo_errore=err,
        )
        if err:
            logger.warning('Webhook %s → %s fallito: %s', hook.name, hook.url, err)
