"""Autenticazione a token per le view dell'API.

`@token_auth_required` legge l'header `Authorization: Bearer <token>`,
risolve un `ApiToken` valido e popola `request.api_user`.
"""
from __future__ import annotations

from functools import wraps

from django.http import JsonResponse
from django.utils import timezone

from .models import ApiToken


def _extract_token(request) -> str | None:
    auth = request.META.get('HTTP_AUTHORIZATION', '')
    if auth.startswith('Bearer '):
        return auth[7:].strip()
    return request.GET.get('api_token')  # fallback comodo per test


def token_auth_required(view):
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        raw = _extract_token(request)
        if not raw:
            return JsonResponse({'error': 'missing_token'}, status=401)
        try:
            tok = ApiToken.objects.select_related('user').get(token=raw, revoked=False)
        except ApiToken.DoesNotExist:
            return JsonResponse({'error': 'invalid_token'}, status=401)
        if not tok.user.is_active:
            return JsonResponse({'error': 'user_disabled'}, status=401)
        tok.last_used_at = timezone.now()
        tok.save(update_fields=['last_used_at'])
        request.api_token = tok
        request.api_user = tok.user
        return view(request, *args, **kwargs)
    return _wrapped
