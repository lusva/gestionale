"""Prepare an account for its own company, without moving business data."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from anagrafiche.models import AnagraficaAzienda
from audit.models import AuditLog, Azione
from audit.utils import get_current_request, set_current_request
from core_settings.models import Organizzazione
from tenancy.context import azienda_context, current_azienda_id
from tenancy.models import Azienda, TenantModel

from .models import Profile, Ruolo, StatoUtente
from .tenancy import utenti_azienda


def motivo_blocco_nuova_azienda(user, actor, profile=None):
    """Return a reason for refusing assignment; inspect persisted state only."""
    if not actor.is_active or not actor.is_superuser:
        raise PermissionDenied
    if settings.SESSION_ENGINE != 'django.contrib.sessions.backends.db':
        return 'L’assegnazione richiede la gestione delle sessioni nel database.'
    if not utenti_azienda().filter(pk=actor.pk).exists():
        raise PermissionDenied
    if profile is None:
        profile = Profile.objects.filter(user_id=user.pk).first()
    if profile is None or profile.azienda_id != current_azienda_id():
        raise PermissionDenied
    if user.pk == actor.pk or user.is_staff or user.is_superuser:
        return 'Non è possibile assegnare una nuova azienda a un account con accesso all’admin.'
    if not user.is_active or profile.stato != StatoUtente.ATTIVO:
        return 'L’account e il profilo devono essere attivi.'
    if not user.has_usable_password():
        return 'Imposta prima una password per questo account.'
    if profile.avatar or any((profile.telefono, profile.qualifica, profile.reparto, profile.sede)):
        return 'Il profilo contiene già dati aziendali. Crea un nuovo utente per la nuova azienda.'
    if user.groups.exists() or user.user_permissions.exists():
        return 'L’account ha gruppi o permessi Django assegnati. Crea un nuovo utente per la nuova azienda.'
    # Login-only use can be reassigned. Business history must stay attached to
    # its original company, so any other authored audit event blocks the move.
    if AuditLog.all_objects.filter(actor_id=user.pk).exclude(
        azienda_id=profile.azienda_id, target_type='User', target_id=str(user.pk),
        azione__in=[Azione.LOGIN, Azione.LOGOUT, Azione.UPDATE],
    ).exists():
        return 'L’account ha attività aziendali registrate. Crea un nuovo utente per la nuova azienda.'
    # Inspect every reverse FK, including tokens, audit actors and assignments.
    # Scoped reverse managers would hide references belonging to other tenants.
    for relation in get_user_model()._meta.related_objects:
        model = relation.related_model
        if model in (Profile, AuditLog):
            continue
        manager = model.all_objects if issubclass(model, TenantModel) else model._default_manager
        if manager.filter(**{relation.field.name: user.pk}).exists():
            return 'L’account ha dati o attività collegati. Crea un nuovo utente per la nuova azienda.'
    return ''


@transaction.atomic
def prepara_azienda_per_utente_esistente(user, actor):
    # Lock and reload instead of trusting the confirmation page or its cached
    # profile. Concurrent login writes to User must finish before this check.
    user = utenti_azienda().select_for_update().get(pk=user.pk)
    profile = Profile.objects.select_for_update().get(user_id=user.pk)
    reason = motivo_blocco_nuova_azienda(user, actor, profile)
    if reason:
        raise ValidationError(reason)
    original_company_id = profile.azienda_id
    # Keep authentication history in the original tenant without leaving a
    # cross-company actor FK. No audit event is deleted or moved.
    login_history = AuditLog.all_objects.filter(actor_id=user.pk)
    login_history.filter(actor_label='').update(actor_label=user.get_username())
    login_history.update(actor=None)
    # Django's DB sessions include the authenticated user ID. Revoke only this
    # user's sessions, including legacy sessions without a company binding.
    session_ids = [
        session.pk for session in Session.objects.iterator()
        if str(session.get_decoded().get('_auth_user_id', '')) == str(user.pk)
    ]
    Session.objects.filter(pk__in=session_ids).delete()
    azienda = Azienda.objects.create(nome='Azienda da configurare', configurata=False)
    previous_request = get_current_request()
    try:
        set_current_request(None)
        with azienda_context(azienda.pk):
            AnagraficaAzienda.objects.create(ragione_sociale='')
            Organizzazione.objects.create(nome=azienda.nome)
            # Deliberate, narrowly guarded exception to immutable membership.
            # Credentials and every business record remain untouched.
            updated = Profile.all_objects.filter(
                pk=profile.pk, azienda_id=original_company_id,
            ).update(azienda_id=azienda.pk, ruolo=Ruolo.AMMINISTRATORE)
            if updated != 1:
                raise ValidationError('L’assegnazione dell’utente è cambiata. Ricarica la pagina.')
            AuditLog.objects.create(
                azione=Azione.CREATE, target_type='Profile', target_id=str(profile.pk),
                target_label=user.username, meta={'sezione': 'Preparazione nuova azienda'},
            )
    finally:
        set_current_request(previous_request)
    AuditLog.objects.create(
        actor=actor, actor_label=actor.get_username(), azione=Azione.UPDATE,
        target_type='User', target_id=str(user.pk), target_label=user.username,
        meta={'sezione': 'Assegnazione nuova azienda', 'nuova_azienda_id': azienda.pk},
    )
    return azienda
