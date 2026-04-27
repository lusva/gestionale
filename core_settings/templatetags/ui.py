"""UI helper tags: Lucide-style SVG icons, badges, avatars, number formatting.

Registered as a template builtin so every template can use these without {% load %}.
"""
from __future__ import annotations

from decimal import Decimal

from django import template
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

register = template.Library()


ICON_PATHS = {
    'dashboard': '<rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/>',
    'users': '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    'user': '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    'briefcase': '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
    'target': '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    'chart': '<path d="M3 3v18h18"/><path d="M7 15l4-4 4 3 5-7"/>',
    'doc': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h5"/>',
    'settings': '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    'bell': '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
    'search': '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    'plus': '<path d="M12 5v14"/><path d="M5 12h14"/>',
    'filter': '<path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z"/>',
    'arrowRight': '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
    'arrowUp': '<path d="M12 19V5"/><path d="m5 12 7-7 7 7"/>',
    'arrowDown': '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
    'chevronDown': '<path d="m6 9 6 6 6-6"/>',
    'chevronRight': '<path d="m9 18 6-6-6-6"/>',
    'chevronLeft': '<path d="m15 18-6-6 6-6"/>',
    'more': '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    'mail': '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m2 7 10 6 10-6"/>',
    'phone': '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>',
    'building': '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 22v-4h6v4"/><path d="M8 6h.01"/><path d="M16 6h.01"/><path d="M12 6h.01"/><path d="M8 10h.01"/><path d="M16 10h.01"/><path d="M12 10h.01"/><path d="M8 14h.01"/><path d="M16 14h.01"/><path d="M12 14h.01"/>',
    'calendar': '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4"/><path d="M8 2v4"/><path d="M3 10h18"/>',
    'euro': '<path d="M18.5 4.5A8 8 0 1 0 18.5 19.5"/><path d="M4 10h11"/><path d="M4 14h11"/>',
    'check': '<path d="M20 6 9 17l-5-5"/>',
    'x': '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    'sun': '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
    'moon': '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    'logout': '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
    'edit': '<path d="M17 3a2.85 2.85 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
    'trash': '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    'download': '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    'upload': '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
    'star': '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    'tag': '<path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><circle cx="7" cy="7" r="1"/>',
    'clock': '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    'pin': '<path d="M12 13c1.66 0 3-1.34 3-3V6c0-1.66-1.34-3-3-3s-3 1.34-3 3v4c0 1.66 1.34 3 3 3z"/><path d="M12 13v9"/>',
    'lock': '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    'eye': '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
    'menu': '<line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/>',
    'grid': '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>',
    'list': '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
    'zap': '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    'trendUp': '<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',
    'trendDown': '<polyline points="22 17 13.5 8.5 8.5 13.5 2 7"/><polyline points="16 17 22 17 22 11"/>',
}


@register.simple_tag
def icon(name, size=16, stroke=1.6, cls=''):
    """Render a Lucide-style line icon.

    Usage: {% icon 'users' size=18 cls='text-muted' %}
    """
    path = ICON_PATHS.get(name)
    if not path:
        return ''
    class_attr = f' class="icon {cls}"' if cls else ' class="icon"'
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="{stroke}" '
        f'stroke-linecap="round" stroke-linejoin="round"{class_attr}>{path}</svg>'
    )
    return mark_safe(svg)


STATO_BADGE_MAP = {
    'attivo': 'badge-success',
    'prospect': 'badge-info',
    'attesa': 'badge-warn',
    'inattivo': 'badge-neutral',
    'sospeso': 'badge-danger',
    'invitato': 'badge-warn',
}


@register.simple_tag
def stato_badge(stato_value, stato_label=None):
    """Render a stato badge. Pass machine value + optional display label."""
    key = (stato_value or '').lower()
    cls = STATO_BADGE_MAP.get(key, 'badge-neutral')
    label = stato_label or stato_value or '—'
    return mark_safe(f'<span class="badge {cls}"><span class="dot"></span>{label}</span>')


STADIO_BADGE_MAP = {
    'nuova': ('badge-neutral', 'var(--ink-3)'),
    'qualificata': ('badge-info', 'var(--info)'),
    'proposta': ('badge-warn', 'var(--warn)'),
    'negoziazione': ('badge-info', 'var(--brand-violet)'),
    'chiusa_win': ('badge-success', 'var(--success)'),
    'chiusa_lost': ('badge-danger', 'var(--danger)'),
}


@register.simple_tag
def stadio_badge(stadio_value, stadio_label=None):
    key = (stadio_value or '').lower()
    cls, color = STADIO_BADGE_MAP.get(key, ('badge-neutral', 'var(--ink-3)'))
    label = stadio_label or stadio_value or '—'
    return mark_safe(
        f'<span class="badge {cls}"><span class="dot" style="background:{color}"></span>{label}</span>'
    )


@register.simple_tag
def avatar(initials, size='md', tint='brand'):
    """Render an avatar bubble with initials.

    size: sm|md|lg  |  tint: brand|neutral|warn|info
    """
    size_cls = {'sm': 'avatar-sm', 'md': '', 'lg': 'avatar-lg'}.get(size, '')
    tint_styles = {
        'brand': 'background:var(--brand-grad);color:white;',
        'neutral': 'background:var(--surface-3);color:var(--ink-2);',
        'warn': 'background:var(--warn-bg);color:var(--warn);',
        'info': 'background:var(--info-bg);color:var(--info);',
    }
    style = tint_styles.get(tint, tint_styles['brand'])
    initials = (initials or '?')[:3].upper()
    return mark_safe(
        f'<span class="avatar {size_cls}" style="{style}">{initials}</span>'
    )


@register.filter
def euro(value, decimals=0):
    """Format a number as Italian euro. {{ 1234.56|euro }} -> '€ 1.235'."""
    if value in (None, ''):
        return '—'
    try:
        n = Decimal(value)
    except (ValueError, TypeError):
        return value
    decimals = int(decimals)
    q = Decimal(10) ** -decimals if decimals else Decimal(1)
    n = n.quantize(q)
    intpart, _, frac = f'{n:f}'.partition('.')
    neg = intpart.startswith('-')
    intpart = intpart.lstrip('-')
    out = ''
    while len(intpart) > 3:
        out = '.' + intpart[-3:] + out
        intpart = intpart[:-3]
    out = intpart + out
    if decimals:
        out += ',' + (frac or '0' * decimals)[:decimals]
    return ('-' if neg else '') + '€ ' + out


@register.filter
def euro_compact(value):
    """Format as € 4.2k / € 487k / € 2.8M."""
    if value in (None, ''):
        return '—'
    try:
        n = float(value)
    except (ValueError, TypeError):
        return value
    abs_n = abs(n)
    if abs_n >= 1_000_000:
        return f'€ {n/1_000_000:.1f}M'.replace('.', ',')
    if abs_n >= 1_000:
        return f'€ {n/1_000:.1f}k'.replace('.', ',')
    return f'€ {n:.0f}'


@register.filter
def pct(value):
    if value in (None, ''):
        return '—'
    return f'{value}%'


@register.simple_tag
def sparkline(points, w=80, h=28, up=True):
    """Render a tiny SVG sparkline. points must be iterable of numbers."""
    try:
        pts = list(points or [])
    except TypeError:
        return ''
    if not pts:
        return ''
    vmax = max(pts)
    vmin = min(pts)
    span = (vmax - vmin) or 1
    n = len(pts)
    coords = []
    for i, v in enumerate(pts):
        x = (i * w) / (n - 1) if n > 1 else 0
        y = h - ((v - vmin) / span) * h
        coords.append(f'{"M" if i == 0 else "L"}{x:.1f},{y:.1f}')
    d = ' '.join(coords)
    color = 'var(--brand-teal)' if up else 'var(--danger)'
    svg = (
        f'<svg width="{w}" height="{h}" style="overflow:visible">'
        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="1.5" '
        f'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )
    return mark_safe(svg)


@register.filter
def get_item(d, key):
    """Dictionary lookup by variable key. {{ mydict|get_item:k }}."""
    if not isinstance(d, dict):
        return ''
    return d.get(key, '')


@register.filter
def attr(obj, name):
    """Variable attribute lookup: {{ obj|attr:'field_name' }}.

    Usato dai template generici per stampare colonne di tabella in cui il
    nome del campo è parametrico (vedi anagrafiche/_anag_table.html).
    Cerca un attributo (anche metodo callable senza argomenti, p.es.
    ``get_<field>_display``) e restituisce stringa vuota se non esiste.
    """
    try:
        value = getattr(obj, name)
    except (AttributeError, TypeError):
        return ''
    if callable(value):
        try:
            value = value()
        except TypeError:
            return ''
    return value


@register.filter
def index(seq, i):
    """List indexing for templates: {{ items|index:forloop.counter0 }}."""
    try:
        return seq[int(i)]
    except (IndexError, ValueError, TypeError):
        return ''


@register.filter
def multiply(value, arg):
    try:
        return float(value) * float(arg)
    except (TypeError, ValueError):
        return ''


@register.filter
def divide(value, arg):
    try:
        arg = float(arg)
        return (float(value) / arg) if arg else 0
    except (TypeError, ValueError):
        return ''
