"""I riferimenti all'utente globale vanno filtrati anche nei widget admin."""
from django.conf import settings
from django.contrib import admin

from accounts.tenancy import utenti_azienda


def _is_user_relation(field):
    model = getattr(getattr(field, 'remote_field', None), 'model', None)
    return model is not None and model._meta.label_lower == settings.AUTH_USER_MODEL.lower()


class AziendaUserFilter(admin.RelatedFieldListFilter):
    def field_choices(self, field, request, model_admin):
        return [(user.pk, str(user)) for user in utenti_azienda().order_by('username')]


class TenantAdminMixin:
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if _is_user_relation(db_field):
            kwargs['queryset'] = utenti_azienda()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if _is_user_relation(db_field):
            kwargs['queryset'] = utenti_azienda()
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def get_list_filter(self, request):
        filters = []
        for entry in super().get_list_filter(request):
            if isinstance(entry, str) and '__' not in entry:
                field = self.model._meta.get_field(entry)
                if _is_user_relation(field):
                    entry = (entry, AziendaUserFilter)
            filters.append(entry)
        return filters
