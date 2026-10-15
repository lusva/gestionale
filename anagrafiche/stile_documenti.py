"""Colori dei documenti stampati (fatture, offerte, ordini, DDT, note di
credito).

L'amministratore sceglie un solo colore d'accento in Impostazioni › Documenti;
qui se ne ricavano le tinte usate dal template PDF: una più scura per i
titoli, una media per i filetti, una tenue per gli sfondi e il colore del
testo da scrivere sopra l'accento (bianco, o scuro se l'accento è chiaro).
"""
from __future__ import annotations

import re

COLORE_DOCUMENTI_PREDEFINITO = '#5b21b6'

_HEX = re.compile(r'^#[0-9a-fA-F]{6}$')
_BIANCO = (255, 255, 255)
_NERO = (0, 0, 0)


def _rgb(colore: str) -> tuple[int, int, int]:
    if not colore or not _HEX.match(colore):
        colore = COLORE_DOCUMENTI_PREDEFINITO
    return tuple(int(colore[i:i + 2], 16) for i in (1, 3, 5))


def _hex(rgb) -> str:
    return '#{:02x}{:02x}{:02x}'.format(*rgb)


def _mescola(rgb, verso, quota: float):
    """``rgb`` spostato di ``quota`` (0–1) verso il colore ``verso``."""
    return tuple(round(c + (v - c) * quota) for c, v in zip(rgb, verso))


def _luminanza(rgb) -> float:
    def lineare(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lineare(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def palette(colore: str | None) -> dict[str, str]:
    """Le tinte del documento a partire dal colore d'accento (``#RRGGBB``;
    se manca o non è valido, quello predefinito)."""
    rgb = _rgb(colore or '')
    # testo bianco sull'accento finché il contrasto regge (WCAG ≥ 4.5)
    su_accento = '#ffffff' if (1.05 / (_luminanza(rgb) + 0.05)) >= 4.5 else '#1d1b26'
    return {
        'accento': _hex(rgb),
        'scuro': _hex(_mescola(rgb, _NERO, 0.35)),
        'medio': _hex(_mescola(rgb, _BIANCO, 0.70)),
        'tenue': _hex(_mescola(rgb, _BIANCO, 0.94)),
        'su_accento': su_accento,
    }
