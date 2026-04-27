"""Helper per scrivere record di audit.

`log(request, azione, target=None, meta=None)` è il singolo entry point
usato sia dai signal sia dai codici di view (import/export).

Il middleware `ThreadLocalRequestMiddleware` espone la request corrente
tramite `get_current_request()` cosicché i signal possano recuperare
actor + IP senza che i modelli debbano accedere a request.
"""
from __future__ import annotations

import threading
from typing import Any

from .models import AuditLog


_storage = threading.local()


def set_current_request(request):
    _storage.request = request


def clear_current_request():
    if hasattr(_storage, 'request'):
        del _storage.request


def get_current_request():
    return getattr(_storage, 'request', None)


def _client_ip(request) -> str | None:
    if not request:
        return None
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
    return xff or request.META.get('REMOTE_ADDR')


def _actor_label(user) -> str:
    if not user or not getattr(user, 'is_authenticated', False):
        return 'anonymous'
    return user.email or user.get_username() or f'user#{user.pk}'


def log(
    azione: str,
    *,
    target: Any = None,
    target_type: str = '',
    target_id: str = '',
    target_label: str = '',
    request=None,
    meta: dict | None = None,
):
    """Scrivi un record AuditLog.

    `target` può essere un'istanza modello (ne deriva type/id/label), oppure
    fornisci esplicitamente i campi `target_type`/`target_id`/`target_label`.
    """
    if request is None:
        request = get_current_request()
    user = getattr(request, 'user', None) if request else None

    if target is not None and not target_type:
        target_type = target.__class__.__name__
        target_id = str(getattr(target, 'pk', '') or '')
        target_label = str(target)[:200]

    return AuditLog.objects.create(
        actor=user if (user and getattr(user, 'is_authenticated', False)) else None,
        actor_label=_actor_label(user)[:150],
        azione=azione,
        target_type=target_type[:50],
        target_id=str(target_id)[:50],
        target_label=target_label[:200],
        ip=_client_ip(request),
        meta=meta or {},
    )
