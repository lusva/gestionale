from django.contrib import admin

from .models import EsecuzioneComando, Organizzazione


@admin.register(Organizzazione)
class OrganizzazioneAdmin(admin.ModelAdmin):
    list_display = ('nome', 'piano', 'posti_totali', 'updated_at')


@admin.register(EsecuzioneComando)
class EsecuzioneComandoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ultimo_avvio', 'ultima_fine', 'esito', 'in_corso_dal')
    readonly_fields = ('nome', 'ultimo_avvio', 'ultima_fine', 'esito', 'riepilogo')
