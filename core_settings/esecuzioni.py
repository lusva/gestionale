"""Registro e lucchetto dei comandi periodici (timer systemd).

    with esecuzione('sdi_pull') as stato:
        ...
        stato.riepilogo = '3 notifiche applicate'

Registra inizio, fine ed esito in ``EsecuzioneComando``; se lo stesso comando
è già in corso solleva ``GiaInCorso``. Il lucchetto scade da solo dopo
``scade_dopo`` (un processo ucciso a metà non lo lascia chiuso per sempre).
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import EsecuzioneComando


class GiaInCorso(Exception):
    pass


@dataclass
class Stato:
    esito: str = 'ok'
    riepilogo: str = ''


@contextmanager
def esecuzione(nome: str, *, scade_dopo: timedelta = timedelta(minutes=30)):
    ora = timezone.now()
    EsecuzioneComando.objects.get_or_create(nome=nome)
    presa = EsecuzioneComando.objects.filter(nome=nome).filter(
        Q(in_corso_dal__isnull=True) | Q(in_corso_dal__lt=ora - scade_dopo),
    ).update(in_corso_dal=ora, ultimo_avvio=ora)
    if not presa:
        raise GiaInCorso(nome)
    stato = Stato()
    try:
        yield stato
    except BaseException as exc:
        stato.esito = 'errore'
        stato.riepilogo = stato.riepilogo or str(exc) or exc.__class__.__name__
        raise
    finally:
        EsecuzioneComando.objects.filter(nome=nome).update(
            in_corso_dal=None, ultima_fine=timezone.now(),
            esito=stato.esito, riepilogo=stato.riepilogo[:2000],
        )


def ultima(nome: str) -> EsecuzioneComando | None:
    return EsecuzioneComando.objects.filter(nome=nome).first()
