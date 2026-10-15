from django.conf import settings
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError
from django.urls import reverse_lazy

from .models import Profile, StatoUtente


class AziendaLoginMixin:
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        # Il login avviene prima di selezionare l'azienda della richiesta.
        # Questo lookup globale serve esclusivamente a verificare l'identità
        # già autenticata; non prende l'azienda da dati inviati dal browser.
        profile = Profile.all_objects.select_related('azienda').filter(user=user).first()
        if (profile is None or not profile.azienda_id or not profile.azienda.attiva
                or profile.stato == StatoUtente.SOSPESO):
            raise ValidationError(
                'Accesso non disponibile per questo account.', code='azienda_non_disponibile',
            )


class AziendaAuthenticationForm(AziendaLoginMixin, AuthenticationForm):
    pass


class AziendaAdminAuthenticationForm(AziendaLoginMixin, AdminAuthenticationForm):
    pass


class LoginView(auth_views.LoginView):
    template_name = 'registration/login.html'
    redirect_authenticated_user = True
    authentication_form = AziendaAuthenticationForm

    def form_valid(self, form):
        response = super().form_valid(form)
        # "Ricordami": sessione di SESSION_COOKIE_AGE (30 giorni),
        # altrimenti SESSION_DURATA_STANDARD (1 giorno), anche chiudendo il browser.
        if self.request.POST.get('remember'):
            self.request.session.set_expiry(settings.SESSION_COOKIE_AGE)
        else:
            self.request.session.set_expiry(settings.SESSION_DURATA_STANDARD)
        return response


class LogoutView(auth_views.LogoutView):
    next_page = reverse_lazy('accounts:login')
