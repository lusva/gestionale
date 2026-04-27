from django.contrib import admin

from .models import Organizzazione


@admin.register(Organizzazione)
class OrganizzazioneAdmin(admin.ModelAdmin):
    list_display = ('nome', 'piano', 'posti_totali', 'updated_at')
