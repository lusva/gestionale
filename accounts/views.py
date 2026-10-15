from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_http_methods

from .forms import ConfiguraAziendaForm
from .models import Profile, StatoUtente
from .permissions import has_perm


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

    def get_success_url(self):
        profile = Profile.all_objects.select_related('azienda').filter(user_id=self.request.user.pk).first()
        if profile is not None and not profile.azienda.configurata:
            return reverse('accounts:configura_azienda')
        return super().get_success_url()

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


@login_required
@require_http_methods(['GET', 'POST'])
def configura_azienda(request):
    """Configure only the empty company already assigned to this account."""
    from anagrafiche.models import AnagraficaAzienda
    from audit.models import Azione
    from audit.utils import log
    from core_settings.models import Organizzazione
    from tenancy.models import Azienda

    if not has_perm(request.user, 'impostazioni.modifica'):
        raise PermissionDenied('La configurazione deve essere completata dall’amministratore della tua azienda.')

    if request.method == 'POST':
        with transaction.atomic():
            azienda = Azienda.objects.select_for_update().get(pk=request.azienda_id)
            if azienda.configurata:
                return redirect('dashboard:home')
            anagrafica = AnagraficaAzienda.objects.first() or AnagraficaAzienda()
            form = ConfiguraAziendaForm(request.POST, instance=anagrafica)
            if form.is_valid():
                anagrafica = form.save()
                azienda.nome = anagrafica.ragione_sociale
                azienda.configurata = True
                azienda.save(update_fields=['nome', 'configurata'])
                organizzazione = Organizzazione.current()
                organizzazione.nome = azienda.nome
                organizzazione.save(update_fields=['nome', 'updated_at'])
                log(Azione.UPDATE, request=request, target=anagrafica,
                    meta={'sezione': 'Prima configurazione azienda'})
                messages.success(request, 'La tua azienda è pronta. Benvenuto nel gestionale!')
                return redirect('dashboard:home')
    else:
        azienda = Azienda.objects.get(pk=request.azienda_id)
        if azienda.configurata:
            return redirect('dashboard:home')
        form = ConfiguraAziendaForm(instance=AnagraficaAzienda.objects.first() or AnagraficaAzienda())
    return render(request, 'accounts/configura_azienda.html', {'form': form})
