from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy


class LoginView(auth_views.LoginView):
    template_name = 'registration/login.html'
    redirect_authenticated_user = True

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
