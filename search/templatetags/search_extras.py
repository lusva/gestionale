"""Template tag `highlight` per la ricerca globale.

Uso: `{{ testo|highlight:q }}` wrappa le occorrenze di `q` in `<mark>`.

Case-insensitive, sicuro per HTML (escape dell'input prima di iniettare).
"""
from __future__ import annotations

import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter(name='highlight', is_safe=True)
def highlight(value, query):
    if not value or not query:
        return value
    text = str(value)
    q = str(query).strip()
    if not q:
        return text
    pattern = re.compile(re.escape(q), re.IGNORECASE)
    escaped = escape(text)
    # Dopo l'escape, il query potrebbe essere sanitizzato diversamente.
    # Lavoriamo sull'escape per essere safe-by-default.
    q_escaped = escape(q)
    if not q_escaped:
        return escaped
    pattern = re.compile(re.escape(q_escaped), re.IGNORECASE)
    highlighted = pattern.sub(
        lambda m: f'<mark>{m.group(0)}</mark>',
        escaped,
    )
    return mark_safe(highlighted)
