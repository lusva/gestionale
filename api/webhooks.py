"""Delivery outbound di webhook.

`dispatch(evento, payload)` invia POST JSON a tutti i Webhook attivi che
accettano quell'evento. Firma HMAC-SHA256 se il webhook ha un secret.

Delivery sincrono: accettabile per un seed di ~10 webhook. Per traffici
seri usare Celery/RQ con retry.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request

from django.utils import timezone

from .models import Webhook


logger = logging.getLogger(__name__)
TIMEOUT_S = 5


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()


def dispatch(evento: str, payload: dict):
    """Invia `payload` a tutti i Webhook interessati a `evento`."""
    hooks = Webhook.objects.filter(attivo=True)
    for h in hooks:
        if not h.accetta(evento):
            continue
        _deliver(h, evento, payload)


def _deliver(hook: Webhook, evento: str, payload: dict):
    body = json.dumps({'evento': evento, 'data': payload, 'at': timezone.now().isoformat()}).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'gestionale-crm/1.0 webhook',
        'X-CRM-Event': evento,
    }
    if hook.secret:
        headers['X-CRM-Signature'] = f'sha256={_sign(hook.secret, body)}'

    req = urllib.request.Request(hook.url, data=body, headers=headers, method='POST')
    status = None
    err = ''
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
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
