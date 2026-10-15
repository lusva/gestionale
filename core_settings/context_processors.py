from __future__ import annotations

_MISSING = object()


def _azienda(request):
    """``AnagraficaAzienda.objects.first()`` memoizzato sulla request, così i
    context processor non ripetono la stessa query ad ogni render."""
    cached = getattr(request, '_anagrafica_azienda_cache', _MISSING)
    if cached is not _MISSING:
        return cached
    try:
        from anagrafiche.models import AnagraficaAzienda
        az = AnagraficaAzienda.objects.first()
    except Exception:
        az = None
    try:
        request._anagrafica_azienda_cache = az
    except Exception:
        pass
    return az


def ui_preferences(request):
    """Expose tema/densita e dati dell'azienda configurata in
    ``AnagraficaAzienda`` ad ogni template (nome + URL del logo).

    Priorità tema/densità: profilo utente autenticato > cookie > default.
    """
    theme = 'light'
    density = 'normal'
    azienda_nome = ''
    azienda_logo_url = ''

    theme = request.COOKIES.get('gest_theme', theme)
    density = request.COOKIES.get('gest_density', density)

    # Il profilo dell'utente autenticato ha la precedenza sul cookie.
    if getattr(request, 'user', None) and request.user.is_authenticated:
        profile = getattr(request.user, 'profile', None)
        if profile:
            theme = profile.tema or theme
            density = profile.densita or density

    try:
        az = None if _frammento_htmx(request) else _azienda(request)
        if az is not None:
            azienda_nome = (az.ragione_sociale or '').strip()
            if az.logo:
                # copia ridotta: l'originale può pesare decine di MB
                from anagrafiche.logo import LATO_SIDEBAR, logo_ridotto_url
                azienda_logo_url = logo_ridotto_url(az.logo, LATO_SIDEBAR)
    except Exception:
        pass

    return {
        'ui_theme': theme if theme in {'light', 'dark'} else 'light',
        'ui_density': density if density in {'compact', 'normal', 'comfy'} else 'normal',
        'azienda_nome': azienda_nome,
        'azienda_logo_url': azienda_logo_url,
    }


def user_perms(request):
    """Espone `can(request.user, 'perm.key')` ai template.

    Nei template:
        {% if 'clienti.elimina'|can:request %}...{% endif %}
    oppure tramite la mappa `user_perms_map` (dict perm→bool), utile per i menu.
    """
    from accounts.permissions import PERM_MATRIX, has_perm

    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {'user_perms_map': {}}

    perms_map = {key: has_perm(request.user, key) for key in PERM_MATRIX}
    return {'user_perms_map': perms_map}


def _frammento_htmx(request) -> bool:
    """True per le risposte HTMX parziali (riepilogo live, lista filtrata…):
    non hanno sidebar né topbar, i loro dati non servono."""
    htmx = getattr(request, 'htmx', None)
    return bool(htmx) and not htmx.boosted


def sidebar_counts(request):
    """Counters shown in the sidebar (clienti, contatti, opportunita) +
    flag ``modulo_magazzino_attivo`` dall'AnagraficaAzienda (se configurata)."""
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {'nav_counts': {}, 'modulo_magazzino_attivo': False}
    if _frammento_htmx(request):
        return {'nav_counts': {}, 'modulo_magazzino_attivo': True}

    counts = {}
    try:
        from clienti.models import Cliente, Contatto
        from opportunita.models import Opportunita, Stadio

        counts = {
            'clienti': Cliente.objects.count(),
            'contatti': Contatto.objects.count(),
            'opportunita': Opportunita.objects.exclude(
                stadio__in=[Stadio.CHIUSA_WIN, Stadio.CHIUSA_LOST],
            ).count(),
        }
    except Exception:
        counts = {}

    modulo_magazzino = True
    az = _azienda(request)
    if az is not None:
        modulo_magazzino = bool(getattr(az, 'modulo_magazzino_attivo', True))

    return {
        'nav_counts': counts,
        'modulo_magazzino_attivo': modulo_magazzino,
    }
