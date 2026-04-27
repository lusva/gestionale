from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import Profile


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'Profilo'
    fk_name = 'user'


class UserAdmin(BaseUserAdmin):
    inlines = [ProfileInline]
    list_display = ('username', 'email', 'first_name', 'last_name', 'ruolo', 'stato_profilo', 'is_staff')

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
