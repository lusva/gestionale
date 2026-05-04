from django.contrib import admin

from .models import (
    RimborsoChilometrico, ScadenzaFiscale, ScadenzaSpesa, SpesaRicorrente,
)


@admin.register(ScadenzaFiscale)
class ScadenzaFiscaleAdmin(admin.ModelAdmin):
    list_display = ('data_scadenza', 'tipo', 'descrizione', 'importo', 'pagata')
    list_filter = ('tipo', 'pagata')
    search_fields = ('descrizione',)
    date_hierarchy = 'data_scadenza'


@admin.register(SpesaRicorrente)
class SpesaRicorrenteAdmin(admin.ModelAdmin):
    list_display = ('descrizione', 'periodicita', 'importo', 'attiva')
    list_filter = ('periodicita', 'attiva')
    search_fields = ('descrizione',)


@admin.register(ScadenzaSpesa)
class ScadenzaSpesaAdmin(admin.ModelAdmin):
    list_display = ('spesa', 'data_scadenza', 'importo', 'pagata')
    list_filter = ('pagata',)
    date_hierarchy = 'data_scadenza'


@admin.register(RimborsoChilometrico)
class RimborsoChilometricoAdmin(admin.ModelAdmin):
    list_display = (
        'data', 'amministratore', 'partenza', 'destinazione',
        'km', 'tariffa_km', 'importo', 'stato',
    )
    list_filter = ('stato', 'amministratore')
    search_fields = ('partenza', 'destinazione', 'causale')
    date_hierarchy = 'data'
    readonly_fields = ('importo', 'created_at', 'updated_at')
