from django.contrib import admin

from .models import ApiToken, Webhook


@admin.register(ApiToken)
class ApiTokenAdmin(admin.ModelAdmin):
    list_display = ('name', 'prefisso', 'user', 'scrittura', 'created_at', 'last_used_at', 'revoked')
    list_filter = ('revoked', 'scrittura')
    search_fields = ('name', 'prefisso', 'user__email', 'user__username')
    # Il token non è recuperabile: in DB c'è solo l'hash (mai mostrato).
    exclude = ('token_hash',)
    readonly_fields = ('prefisso', 'created_at', 'last_used_at')


@admin.register(Webhook)
class WebhookAdmin(admin.ModelAdmin):
    list_display = ('name', 'url', 'attivo', 'ultimo_status', 'ultimo_tentativo_at')
    list_filter = ('attivo',)
    search_fields = ('name', 'url')
    readonly_fields = ('ultimo_tentativo_at', 'ultimo_status', 'ultimo_errore', 'created_at')
