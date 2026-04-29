from django.contrib import admin

from .models import (
    Agente,
    AnagraficaAzienda,
    Articolo,
    CassaPrevidenziale,
    CategoriaCosto,
    ContoCorrente,
    EsigibilitaIva,
    FatturazioneElettronica,
    FormePagamento,
    Fornitore,
    Indirizzo,
    PosizioneIva,
    ProfiloFiscale,
    Referente,
    Scadenza,
)


class ScadenzaInline(admin.TabularInline):
    model = Scadenza
    extra = 1


@admin.register(FormePagamento)
class FormePagamentoAdmin(admin.ModelAdmin):
    list_display = ('tipo_pagamento', 'modalita_pagamento', 'conto_corrente_cliente')
    list_filter = ('modalita_pagamento',)
    search_fields = ('tipo_pagamento',)
    inlines = [ScadenzaInline]


@admin.register(Scadenza)
class ScadenzaAdmin(admin.ModelAdmin):
    list_display = (
        'forme_pagamento', 'numero_giorni', 'percentuale',
        'fine_mese', 'numero_giorni_fm',
    )
    list_filter = ('fine_mese', 'forme_pagamento')


@admin.register(PosizioneIva)
class PosizioneIvaAdmin(admin.ModelAdmin):
    list_display = (
        'descrizione', 'aliquota', 'natura',
        'reverse_charge', 'scissione_pagamenti', 'bollo', 'esente',
    )
    list_filter = ('reverse_charge', 'scissione_pagamenti', 'bollo', 'esente')
    search_fields = ('descrizione',)


@admin.register(EsigibilitaIva)
class EsigibilitaIvaAdmin(admin.ModelAdmin):
    list_display = ('tipo', 'attiva')
    list_filter = ('attiva', 'tipo')


@admin.register(CassaPrevidenziale)
class CassaPrevidenzialeAdmin(admin.ModelAdmin):
    list_display = (
        'tipo', 'aliquota_cassa', 'percentuale_imponibile', 'attiva',
    )
    list_filter = ('attiva', 'tipo')


@admin.register(ProfiloFiscale)
class ProfiloFiscaleAdmin(admin.ModelAdmin):
    list_display = ('regime_fiscale', 'esigibilita_iva', 'cassa_previdenziale')
    list_filter = ('regime_fiscale',)


@admin.register(FatturazioneElettronica)
class FatturazioneElettronicaAdmin(admin.ModelAdmin):
    list_display = ('sdi', 'pec')
    search_fields = ('sdi', 'pec')


@admin.register(AnagraficaAzienda)
class AnagraficaAziendaAdmin(admin.ModelAdmin):
    list_display = (
        'ragione_sociale', 'partita_iva', 'codice_fiscale', 'email',
        'modulo_magazzino_attivo',
    )
    search_fields = ('ragione_sociale', 'partita_iva', 'codice_fiscale')
    list_filter = ('modulo_magazzino_attivo',)


class IndirizzoInline(admin.TabularInline):
    model = Indirizzo
    extra = 0
    fk_name = 'fornitore'


class ContoCorrenteInline(admin.TabularInline):
    model = ContoCorrente
    extra = 0
    fk_name = 'fornitore'


@admin.register(Fornitore)
class FornitoreAdmin(admin.ModelAdmin):
    list_display = (
        'ragione_sociale', 'nome', 'cognome', 'partita_iva', 'email',
        'telefono', 'soft_delete',
    )
    list_filter = ('soft_delete',)
    search_fields = (
        'ragione_sociale', 'nome', 'cognome', 'partita_iva', 'codice_fiscale',
    )
    inlines = [IndirizzoInline, ContoCorrenteInline]


@admin.register(Agente)
class AgenteAdmin(admin.ModelAdmin):
    list_display = (
        'nome', 'cognome', 'ragione_sociale', 'provvigione_default', 'email',
    )
    search_fields = ('nome', 'cognome', 'ragione_sociale', 'partita_iva')


@admin.register(Referente)
class ReferenteAdmin(admin.ModelAdmin):
    list_display = ('numero', 'nome', 'cognome')
    search_fields = ('nome', 'cognome')


@admin.register(Indirizzo)
class IndirizzoAdmin(admin.ModelAdmin):
    list_display = (
        'indirizzo', 'comune', 'provincia', 'nazione',
        'cliente', 'fornitore', 'sede',
    )
    list_filter = ('provincia', 'nazione', 'sede')
    search_fields = ('indirizzo', 'comune')


@admin.register(ContoCorrente)
class ContoCorrenteAdmin(admin.ModelAdmin):
    list_display = (
        'banca', 'intestatario', 'iban', 'bic_swift',
        'cliente', 'fornitore', 'anagrafica_azienda', 'default',
    )
    list_filter = ('default',)
    search_fields = ('banca', 'intestatario', 'iban')


@admin.register(Articolo)
class ArticoloAdmin(admin.ModelAdmin):
    list_display = (
        'codice', 'descrizione', 'um', 'prezzo_listino',
        'posizione_iva', 'obsoleto',
    )
    list_filter = ('um', 'obsoleto', 'posizione_iva')
    search_fields = ('codice', 'descrizione')


@admin.register(CategoriaCosto)
class CategoriaCostoAdmin(admin.ModelAdmin):
    list_display = ('codice', 'descrizione', 'ordinamento', 'attiva')
    list_filter = ('attiva',)
    search_fields = ('codice', 'descrizione')
    ordering = ('ordinamento', 'codice')
