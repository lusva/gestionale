"""Bind new login sessions to the membership selected by the server."""
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver


SESSION_AZIENDA_KEY = '_auth_azienda_id'


@receiver(user_logged_in, dispatch_uid='accounts.bind_login_company')
def bind_login_company(sender, request, user, **kwargs):
    from .models import Profile

    azienda_id = Profile.all_objects.filter(user_id=user.pk).values_list(
        'azienda_id', flat=True,
    ).first()
    if azienda_id is not None:
        request.session[SESSION_AZIENDA_KEY] = azienda_id
