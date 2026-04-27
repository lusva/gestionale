from __future__ import annotations


def ui_preferences(request):
    """Expose tema/densita and organization name to every template.

    Priority: authenticated user profile > cookie > defaults.
    """
    theme = 'light'
    density = 'normal'
    org_name = 'AIGIS lab'
    org_suborg = 'Studio Rossi SRL'

    if getattr(request, 'user', None) and request.user.is_authenticated:
        profile = getattr(request.user, 'profile', None)
        if profile:
            theme = profile.tema or theme
            density = profile.densita or density

    theme = request.COOKIES.get('gest_theme', theme)
    density = request.COOKIES.get('gest_density', density)

    try:
        from core_settings.models import Organizzazione
        org = Organizzazione.objects.first()
        if org:
            org_suborg = org.nome
    except Exception:
        pass

    return {
        'ui_theme': theme if theme in {'light', 'dark'} else 'light',
        'ui_density': density if density in {'compact', 'normal', 'comfy'} else 'normal',
        'org_name': org_name,
        'org_suborg': org_suborg,
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


def sidebar_counts(request):
    """Counters shown in the sidebar (clienti, contatti, opportunita) +
    flag ``modulo_magazzino_attivo`` dall'AnagraficaAzienda (se configurata)."""
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {'nav_counts': {}, 'modulo_magazzino_attivo': False}

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
    try:
        from anagrafiche.models import AnagraficaAzienda
        az = AnagraficaAzienda.objects.first()
        if az is not None:
            modulo_magazzino = bool(az.modulo_magazzino_attivo)
    except Exception:
        pass

    return {
        'nav_counts': counts,
        'modulo_magazzino_attivo': modulo_magazzino,
    }
