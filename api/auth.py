"""Autenticazione a token per le view dell'API.

Il token si accetta solo nell'header ``Authorization: Bearer <token>`` (mai
nella query string, che finisce nei log di nginx e nella cronologia). Nel DB
c'è solo il suo hash: vedi ``ApiToken.da_token``.
"""
from __future__ import annotations

from functools import wraps

from django.http import JsonResponse

from .models import ApiToken


def estrai_token(request) -> str:
    auth = request.META.get('HTTP_AUTHORIZATION', '')
    return auth[7:].strip() if auth.startswith('Bearer ') else ''


def token_auth_required(view=None, *, perm: str | None = None):
    """Autenticazione degli endpoint ``/api/v1/`` (sola lettura).

    Si usa come ``@token_auth_required`` o ``@token_auth_required(perm=...)``:
    ``perm`` è un permesso del ruolo (``accounts.permissions``), come in v2.
    """
    from accounts.permissions import has_perm

    def deco(view):
        @wraps(view)
        def _wrapped(request, *args, **kwargs):
            raw = estrai_token(request)
            if not raw:
                return JsonResponse({'error': 'missing_token'}, status=401)
            tok = ApiToken.da_token(raw)
            if tok is None:
                return JsonResponse({'error': 'invalid_token'}, status=401)
            if not tok.user.is_active:
                return JsonResponse({'error': 'user_disabled'}, status=401)
            if perm and not has_perm(tok.user, perm):
                return JsonResponse({'error': 'forbidden', 'permission': perm}, status=403)
            tok.segna_uso()
            request.api_token = tok
            request.api_user = tok.user
            return view(request, *args, **kwargs)
        return _wrapped

    return deco(view) if view is not None else deco


def token_v2(*, scrittura: bool = False, perm: str | None = None):
    """Autenticazione degli endpoint ``/api/v2/``.

    Rispetto a ``token_auth_required``: ``request.user`` diventa l'utente
    del token (così audit e permessi lo vedono come chi ha agito),
    ``scrittura=True`` esige un token abilitato alla scrittura e ``perm``
    un permesso del ruolo (``accounts.permissions``).
    """
    from accounts.permissions import has_perm

    def deco(view):
        @wraps(view)
        def _wrapped(request, *args, **kwargs):
            raw = estrai_token(request)
            if not raw:
                return JsonResponse({'errore': 'token_mancante'}, status=401)
            tok = ApiToken.da_token(raw)
            if tok is None:
                return JsonResponse({'errore': 'token_non_valido'}, status=401)
            if not tok.user.is_active:
                return JsonResponse({'errore': 'utente_disattivato'}, status=401)
            if scrittura and not tok.scrittura:
                return JsonResponse(
                    {'errore': 'token_sola_lettura',
                     'dettaglio': 'Abilita la scrittura sul token in Impostazioni › API.'},
                    status=403)
            if perm and not has_perm(tok.user, perm):
                return JsonResponse({'errore': 'permesso_negato', 'permesso': perm}, status=403)
            tok.segna_uso()
            request.api_token = tok
            request.api_user = tok.user
            request.user = tok.user
            return view(request, *args, **kwargs)
        return _wrapped
    return deco
