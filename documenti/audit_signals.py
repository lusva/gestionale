"""Signal di audit per le testate documento.

Registra create/update/delete su ``audit.AuditLog`` riusando il
``ThreadLocalRequestMiddleware`` per recuperare actor + IP senza che
queste view debbano farlo esplicitamente.
"""
from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from audit.models import Azione
from audit.utils import log as audit_log

from .models import (
    TestataDdt,
    TestataDdtFornitore,
    TestataFattura,
    TestataFatturaAcquisto,
    TestataNotaCredito,
    TestataOfferta,
    TestataOrdine,
)


_TESTATE = (
    TestataOfferta, TestataOrdine, TestataDdt, TestataFattura,
    TestataNotaCredito, TestataDdtFornitore, TestataFatturaAcquisto,
)


def _label(instance) -> str:
    cls = instance.__class__.__name__
    return f'{cls} {instance.numero}/{instance.anno}'


@receiver(post_save)
def _testata_saved(sender, instance, created, **kwargs):
    if sender not in _TESTATE:
        return
    azione = Azione.CREATE if created else Azione.UPDATE
    audit_log(
        azione,
        target=instance,
        target_label=_label(instance),
    )


@receiver(post_delete)
def _testata_deleted(sender, instance, **kwargs):
    if sender not in _TESTATE:
        return
    audit_log(
        Azione.DELETE,
        target=instance,
        target_label=_label(instance),
    )
