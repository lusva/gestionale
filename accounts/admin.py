from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User

from .models import Profile
from .tenancy import utenti_azienda
from .views import AziendaAdminAuthenticationForm


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'Profilo'
    fk_name = 'user'


class UserAdmin(BaseUserAdmin):
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
