"""Provisioning shared by the server command and superuser administration."""
from django.db import transaction

from anagrafiche.models import AnagraficaAzienda
from audit.utils import get_current_request, set_current_request
from core_settings.models import Organizzazione
from tenancy.context import azienda_context
from tenancy.models import Azienda

from .models import Ruolo, StatoUtente


@transaction.atomic
def crea_azienda_per_utente(user, nome=''):
    if not user._state.adding:
        raise ValueError('Il provisioning richiede un nuovo utente.')
    azienda = Azienda.objects.create(
        nome=nome or 'Azienda da configurare', attiva=True, configurata=bool(nome),
    )
    # The administrator belongs to a different company. Automatic creation
    # events in the new company must not store a foreign company's user FK.
    previous_request = get_current_request()
    try:
        set_current_request(None)
        with azienda_context(azienda.pk):
            user.is_staff = False
            user.is_superuser = False
            user.save()
            profile = user.profile
            profile.ruolo = Ruolo.AMMINISTRATORE
            profile.stato = StatoUtente.ATTIVO
            profile.save(update_fields=['ruolo', 'stato'])
            AnagraficaAzienda.objects.create(ragione_sociale=nome)
            Organizzazione.objects.create(nome=azienda.nome)
    finally:
        set_current_request(previous_request)
    return azienda
