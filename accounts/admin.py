from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.urls import reverse

from .forms import CreaUtenteAdminForm
from .models import Profile
from .provisioning import crea_azienda_per_utente
from .tenancy import utenti_azienda
from .views import AziendaAdminAuthenticationForm


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'Profilo'
    fk_name = 'user'


class UserAdmin(BaseUserAdmin):
    add_form = CreaUtenteAdminForm
    inlines = [ProfileInline]
    list_display = ('username', 'email', 'first_name', 'last_name', 'ruolo', 'stato_profilo', 'is_staff')
    # Gruppi e permessi Django sono globali: la gestione applicativa usa i
    # ruoli del profilo, senza esporre o modificare configurazioni condivise.
    filter_horizontal = ()
    list_filter = ('is_staff', 'is_superuser', 'is_active')
    fieldsets = tuple(
        (title, {**options, 'fields': tuple(
            field for field in options['fields'] if field not in ('groups', 'user_permissions')
        )})
        for title, options in BaseUserAdmin.fieldsets
    )

    def get_queryset(self, request):
        return super().get_queryset(request).filter(pk__in=utenti_azienda().values('pk'))

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj is None and request.user.is_superuser:
            return (*fieldsets, ('Accesso aziendale', {'fields': ('destinazione_azienda',)}))
        return fieldsets

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if not request.user.is_superuser and 'destinazione_azienda' in form.base_fields:
            # Declared fields may survive ModelForm's fields selection.
            form.base_fields.pop('destinazione_azienda')
        return form

    def save_model(self, request, obj, form, change):
        nuova = not change and request.POST.get('destinazione_azienda') == 'nuova'
        if nuova:
            if not request.user.is_superuser:
                raise PermissionDenied('Solo il superuser può creare account per una nuova azienda.')
            # Only a validated choice may authorize provisioning.
            if form.cleaned_data.get('destinazione_azienda') != 'nuova':
                raise PermissionDenied
            crea_azienda_per_utente(obj)
            obj._azienda_appena_creata = True
        else:
            super().save_model(request, obj, form, change)

    def response_add(self, request, obj, post_url_continue=None):
        if getattr(obj, '_azienda_appena_creata', False):
            self.message_user(
                request,
                f'Utente “{obj.username}” creato. Comunica le credenziali: al primo accesso inserirà i dati della sua azienda.',
                level='SUCCESS',
            )
            # The new user is outside the current company's user list. Never
            # redirect to its change form or offer a link that would be denied.
            return HttpResponseRedirect(reverse('admin:auth_user_add'))
        return super().response_add(request, obj, post_url_continue)

    def get_inline_instances(self, request, obj=None):
        # In fase di creazione il Profile viene generato dal signal post_save
        # di User: mostrare l'inline causerebbe un secondo INSERT (IntegrityError
        # sul OneToOne). L'inline è disponibile solo in modifica.
        if obj is None:
            return []
        return super().get_inline_instances(request, obj)

    def ruolo(self, obj):
        return getattr(obj.profile, 'get_ruolo_display', lambda: '—')()
    ruolo.short_description = 'Ruolo'

    def stato_profilo(self, obj):
        return getattr(obj.profile, 'get_stato_display', lambda: '—')()
    stato_profilo.short_description = 'Stato'


try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass
admin.site.register(User, UserAdmin)
admin.site.login_form = AziendaAdminAuthenticationForm
try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass
