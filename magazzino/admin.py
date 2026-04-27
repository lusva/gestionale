from django.contrib import admin

from .models import Movimento


@admin.register(Movimento)
class MovimentoAdmin(admin.ModelAdmin):
    list_display = ('data', 'tipo', 'articolo', 'quantita', 'prezzo', 'riga_documento')
    list_filter = ('tipo',)
    search_fields = ('articolo__codice', 'articolo__descrizione', 'descrizione')
    date_hierarchy = 'data'
    autocomplete_fields = ('articolo',)
