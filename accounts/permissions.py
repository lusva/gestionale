"""Permessi RBAC basati su Profile.ruolo.

La matrice è allineata a `core_settings.views.PERMESSI_MATRICE`. I permessi
sono identificati da stringhe e ogni permesso mappa ai tre ruoli:
Amministratore, Account manager, Visualizzatore.

Uso (funzionale):

    from accounts.permissions import require_perm

    @require_perm('clienti.elimina')
    def cliente_delete(request, pk): ...

Uso (CBV):

    from accounts.permissions import PermRequiredMixin

    class ClienteDeleteView(PermRequiredMixin, DeleteView):
        required_perm = 'clienti.elimina'
"""
from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .models import Ruolo


# (perm_key, (ammin, account_manager, visualizzatore))
PERM_MATRIX: dict[str, tuple[bool, bool, bool]] = {
    'clienti.vedi':            (True,  True,  True),
    'clienti.modifica':        (True,  True,  False),
    'clienti.elimina':         (True,  False, False),
    'clienti.importa':         (True,  True,  False),
    'clienti.esporta':         (True,  True,  False),
    'utenti.gestisci':         (True,  False, False),
    'report.vedi':             (True,  True,  True),
    'report.esporta':          (True,  True,  False),
    'opportunita.modifica':    (True,  True,  False),
    'opportunita.elimina':     (True,  False, False),
    'attivita.modifica':       (True,  True,  False),
    'impostazioni.modifica':   (True,  False, False),
    # Documenti (offerte/ordini/ddt/fatture/note credito + ciclo passivo)
    'documenti.vedi':          (True,  True,  True),
    'documenti.modifica':      (True,  True,  False),
    'documenti.elimina':       (True,  False, False),
}


_RUOLO_INDEX = {
    Ruolo.AMMINISTRATORE: 0,
    Ruolo.ACCOUNT_MANAGER: 1,
    Ruolo.VISUALIZZATORE: 2,
}


def has_perm(user, perm: str) -> bool:
    """Ritorna True se l'utente ha il permesso richiesto."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    row = PERM_MATRIX.get(perm)
    if row is None:
        # Permesso sconosciuto: nega per sicurezza.
        return False
    profile = getattr(user, 'profile', None)
    if profile is None:
        return False
    idx = _RUOLO_INDEX.get(profile.ruolo)
    if idx is None:
        return False
    return bool(row[idx])


def require_perm(perm: str):
    """Decoratore per view funzionali: esige login + ruolo che concede `perm`."""
    def deco(view_func):
        @login_required
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not has_perm(request.user, perm):
                messages.error(
                    request,
                    f'Non hai i permessi necessari per questa azione ({perm}).',
                )
                if getattr(request, 'htmx', None):
                    from django.http import HttpResponse
                    return HttpResponse(status=403)
                return redirect('dashboard:home')
            return view_func(request, *args, **kwargs)
        return _wrapped
    return deco


class PermRequiredMixin:
    """Mixin per CBV: verifica Profile.ruolo rispetto a `self.required_perm`."""
    required_perm: str | None = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())
        if self.required_perm and not has_perm(request.user, self.required_perm):
            messages.error(
                request,
                f'Non hai i permessi necessari per questa azione ({self.required_perm}).',
            )
            if getattr(request, 'htmx', None):
                from django.http import HttpResponse
                return HttpResponse(status=403)
            return redirect('dashboard:home')
        return super().dispatch(request, *args, **kwargs)
