"""Signal handlers che registrano eventi audit.

Copre create/update/delete su Cliente, Opportunita, Attivita e User,
più login/logout/login_failed di Django auth.
"""
from django.contrib.auth.signals import (
    user_logged_in, user_logged_out, user_login_failed,
)
from django.contrib.auth.models import User
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from attivita.models import Attivita
from clienti.models import Cliente
from opportunita.models import Opportunita

from .models import Azione
from .utils import log


_TRACKED = (Cliente, Opportunita, Attivita, User)


@receiver(post_save)
def _on_save(sender, instance, created, **kwargs):
    if sender not in _TRACKED:
        return
    # Evita di loggare i nostri stessi AuditLog o la creazione automatica di Profile
    log(
        Azione.CREATE if created else Azione.UPDATE,
        target=instance,
    )


@receiver(post_delete)
def _on_delete(sender, instance, **kwargs):
    if sender not in _TRACKED:
        return
    log(Azione.DELETE, target=instance)


@receiver(user_logged_in)
def _on_login(sender, request, user, **kwargs):
    log(
        Azione.LOGIN,
        target_type='User', target_id=str(user.pk), target_label=str(user),
        request=request,
    )


@receiver(user_logged_out)
def _on_logout(sender, request, user, **kwargs):
    if not user:
        return
    log(
        Azione.LOGOUT,
        target_type='User', target_id=str(user.pk), target_label=str(user),
        request=request,
    )


@receiver(user_login_failed)
def _on_login_failed(sender, credentials, request, **kwargs):
    log(
        Azione.LOGIN_FAILED,
        target_type='User',
        target_label=(credentials or {}).get('username', '')[:200],
        request=request,
        meta={'username_attempt': (credentials or {}).get('username', '')},
    )
