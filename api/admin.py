from django.contrib import admin

from .models import ApiToken, Webhook


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'created_at', 'last_used_at', 'revoked')
    list_filter = ('revoked',)
    search_fields = ('name', 'user__email', 'user__username')
    readonly_fields = ('token', 'created_at', 'last_used_at')


@admin.register(Webhook)
class WebhookAdmin(admin.ModelAdmin):
    list_display = ('name', 'url', 'attivo', 'ultimo_status', 'ultimo_tentativo_at')
    list_filter = ('attivo',)
    search_fields = ('name', 'url')
    readonly_fields = ('ultimo_tentativo_at', 'ultimo_status', 'ultimo_errore', 'created_at')
