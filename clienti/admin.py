from django.contrib import admin

from .models import Cliente, Contatto, Settore, Tag


class ContattoInline(admin.TabularInline):
    model = Contatto
    extra = 1


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('ragione_sociale', 'tipo', 'settore', 'citta', 'stato', 'account_manager')
    list_filter = ('tipo', 'stato', 'settore', 'nazione')
    search_fields = ('ragione_sociale', 'partita_iva', 'codice_fiscale', 'pec')
    autocomplete_fields = ('account_manager',)
    filter_horizontal = ('tags',)
    inlines = [ContattoInline]


@admin.register(Contatto)
class ContattoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'cognome', 'cliente', 'ruolo', 'email', 'primary')
    list_filter = ('primary',)
    search_fields = ('nome', 'cognome', 'email', 'cliente__ragione_sociale')


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('nome', 'colore')
    search_fields = ('nome',)


@admin.register(Settore)
class SettoreAdmin(admin.ModelAdmin):
    list_display = ('nome', 'slug')
    search_fields = ('nome',)
    prepopulated_fields = {'slug': ('nome',)}
