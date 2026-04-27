from django.contrib import admin

from .models import Attivita


@admin.register(Attivita)
class AttivitaAdmin(admin.ModelAdmin):
    list_display = ('titolo', 'tipo', 'cliente', 'data', 'completata', 'owner')
    list_filter = ('tipo', 'completata', 'owner')
    search_fields = ('titolo', 'cliente__ragione_sociale')
    autocomplete_fields = ('cliente', 'opportunita', 'owner')
    date_hierarchy = 'data'
