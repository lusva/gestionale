from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponseBadRequest, HttpResponseNotAllowed, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from .forms import CreaUtenteAdminForm
from .company_assignment import motivo_blocco_nuova_azienda, prepara_azienda_per_utente_esistente
from .models import Profile
from .provisioning import crea_azienda_per_utente
from .tenancy import utenti_azienda
from .views import AziendaAdminAuthenticationForm


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'Profilo'
    fk_name = 'user'
    readonly_fields = ('ultimo_accesso',)


class UserAdmin(BaseUserAdmin):
    add_form = CreaUtenteAdminForm
    inlines = [ProfileInline]
    list_display = ('username', 'email', 'first_name', 'last_name', 'azienda_associata', 'ruolo', 'stato_profilo', 'is_staff')
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
        return (super().get_queryset(request).filter(pk__in=utenti_azienda().values('pk'))
                .select_related('profile__azienda'))

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if obj is not None:
            fields = (*fields, 'azienda_associata', 'crea_utente_altra_azienda', 'last_login')
        return fields

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj is None and request.user.is_superuser:
            return (*fieldsets, ('Accesso aziendale', {'fields': ('destinazione_azienda',)}))
        if obj is not None:
            fields = ('azienda_associata',)
            if request.user.is_superuser:
                fields += ('crea_utente_altra_azienda',)
            return (('Accesso aziendale', {'fields': fields}), *fieldsets)
        return fieldsets

    @admin.display(description='Azienda')
    def azienda_associata(self, obj):
        profile = getattr(obj, 'profile', None)
        if profile is None:
            return 'Nessuna azienda associata'
        azienda = profile.azienda
        stato = 'Configurazione completata' if azienda.configurata else 'Da configurare al primo accesso'
        return format_html('{} (ID {}) — {}', azienda.nome, azienda.pk, stato)

    @admin.display(description='Account per una nuova azienda')
    def crea_utente_altra_azienda(self, obj):
        url = reverse('admin:auth_user_add') + '?destinazione_azienda=nuova'
        assignment_url = reverse('admin:auth_user_nuova_azienda', args=[obj.pk])
        return format_html(
            '<a href="{}">Assegna questo utente a una nuova azienda</a>'
            ' (solo account senza dati o attività aziendali).<br>'
            '<a href="{}">Aggiungi un altro utente per una nuova azienda</a>',
            assignment_url, url,
        )

    def get_urls(self):
        return [path(
            '<int:object_id>/nuova-azienda/',
            self.admin_site.admin_view(self.nuova_azienda_view),
            name='auth_user_nuova_azienda',
        ), *super().get_urls()]

    def nuova_azienda_view(self, request, object_id):
        if not request.user.is_superuser:
            raise PermissionDenied
        if request.method not in ('GET', 'POST'):
            return HttpResponseNotAllowed(['GET', 'POST'])
        user = self.get_object(request, str(object_id))
        if user is None:
            raise Http404
        reason = motivo_blocco_nuova_azienda(user, request.user)
        if request.method == 'POST':
            if request.POST.get('confirm') != 'yes':
                return HttpResponseBadRequest('Conferma l’assegnazione dalla pagina dedicata.')
            try:
                prepara_azienda_per_utente_esistente(user, request.user)
            except User.DoesNotExist:
                raise Http404
            except ValidationError as exc:
                reason = ' '.join(exc.messages)
            else:
                self.log_change(request, user, 'Assegnato a una nuova azienda da configurare.')
                self.message_user(
                    request,
                    f'Utente “{user.username}” assegnato a una nuova azienda. '
                    'Username e password restano gli stessi. Al prossimo accesso compilerà i dati '
                    'della sua azienda. Non compare più nell’elenco utenti di questa azienda.',
                    level='SUCCESS',
                )
                return HttpResponseRedirect(reverse('admin:auth_user_changelist'))
        return TemplateResponse(request, 'admin/accounts/user/nuova_azienda.html', {
            **self.admin_site.each_context(request),
            'title': 'Assegna una nuova azienda', 'opts': self.model._meta,
            'original': user, 'azienda_attuale': user.profile.azienda,
            'motivo_blocco': reason,
            'change_url': reverse('admin:auth_user_change', args=[user.pk]),
        })

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
