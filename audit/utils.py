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

from django.conf import settings

from .models import AuditLog
from tenancy.context import azienda_context, current_azienda_id


_storage = threading.local()


def set_current_request(request):
    _storage.request = request


def clear_current_request():
    if hasattr(_storage, 'request'):
        del _storage.request


def get_current_request():
    return getattr(_storage, 'request', None)


def _client_ip(request) -> str | None:
    """IP del client per l'audit.

    Default: ``REMOTE_ADDR`` (non falsificabile dal client). Gli header
    ``X-Forwarded-For`` sono considerati solo se
    ``settings.AUDIT_TRUST_X_FORWARDED_FOR`` è True (app dietro reverse proxy
    fidato, es. nginx con ``$proxy_add_x_forwarded_for``): in tal caso si usa
    l'ultima voce, cioè quella aggiunta dal proxy (le precedenti possono
    essere forgiate dal client).
    """
    if not request:
        return None
    if getattr(settings, 'AUDIT_TRUST_X_FORWARDED_FOR', False):
        xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
        parts = [p.strip() for p in xff.split(',') if p.strip()]
        if parts:
            return parts[-1]
    return request.META.get('REMOTE_ADDR') or None


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

    azienda_id = current_azienda_id()
    # Login happens inside an anonymous request. Attribute its audit event to
    # the authenticated user's company; failed anonymous attempts have no
    # company and must not be written into another company's log.
    if azienda_id is None and user and user.is_authenticated:
        from accounts.models import Profile
        azienda_id = Profile.all_objects.filter(user_id=user.pk).values_list(
            'azienda_id', flat=True,
        ).first()
    if azienda_id is None:
        return None
    with azienda_context(azienda_id):
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
