"""Accesso agli utenti della sola azienda selezionata dal server."""
from django.contrib.auth import get_user_model

from tenancy.context import current_azienda_id


def utenti_azienda():
    azienda_id = current_azienda_id()
    users = get_user_model().objects.all()
    if azienda_id is None:
        return users.none()
    return users.filter(profile__azienda_id=azienda_id)
