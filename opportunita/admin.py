from django.contrib import admin

from .models import Opportunita


@admin.register(Opportunita)
class OpportunitaAdmin(admin.ModelAdmin):
    list_display = ('titolo', 'cliente', 'valore', 'stadio', 'probabilita', 'chiusura_prevista', 'owner')
    list_filter = ('stadio', 'owner')
    search_fields = ('titolo', 'cliente__ragione_sociale')
    autocomplete_fields = ('cliente', 'owner')
    date_hierarchy = 'chiusura_prevista'
