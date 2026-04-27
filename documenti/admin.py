from django.contrib import admin

from .models import (
    AgenteFattura,
    MessaggioSdi,
    PagamentoScadenzaAcquisto,
    RigaDocumento,
    RigaFatturaAcquisto,
    ScadenzaFattura,
    ScadenzaFatturaAcquisto,
    TestataDdt,
    TestataDdtFornitore,
    TestataFattura,
    TestataFatturaAcquisto,
    TestataNotaCredito,
    TestataOfferta,
    TestataOrdine,
)


# ---------------------------------------------------------------------------
# Inline righe: condivise tra tutte le testate via fk specifica
# ---------------------------------------------------------------------------


class _RigaInlineBase(admin.TabularInline):
    model = RigaDocumento
    extra = 1
    fields = (
        'numero_riga', 'articolo', 'descrizione_libera',
        'quantita', 'um', 'importo_unitario', 'iva',
    )
    autocomplete_fields = ('articolo', 'iva')


class RigaOffertaInline(_RigaInlineBase):
    fk_name = 'testata_offerta'


class RigaOrdineInline(_RigaInlineBase):
    fk_name = 'testata_ordine'


class RigaDdtInline(_RigaInlineBase):
    fk_name = 'testata_ddt'


class RigaFatturaInline(_RigaInlineBase):
    fk_name = 'testata_fattura'


class RigaNotaCreditoInline(_RigaInlineBase):
    fk_name = 'testata_nota_credito'


class RigaDdtFornitoreInline(_RigaInlineBase):
    fk_name = 'testata_ddt_fornitore'


class ScadenzaFatturaInline(admin.TabularInline):
    model = ScadenzaFattura
    extra = 1


class AgenteFatturaInline(admin.TabularInline):
    model = AgenteFattura
    extra = 0
    autocomplete_fields = ('agente',)


# ---------------------------------------------------------------------------
# Testate ciclo attivo
# ---------------------------------------------------------------------------


@admin.register(TestataOfferta)
class TestataOffertaAdmin(admin.ModelAdmin):
    list_display = (
        'numero', 'anno', 'data_documento', 'cliente', 'stato', 'imponibile',
    )
    list_filter = ('stato', 'anno')
    search_fields = ('numero', 'cliente__ragione_sociale')
    autocomplete_fields = ('cliente', 'fornitore', 'forme_pagamento', 'conto_corrente')
    date_hierarchy = 'data_documento'
    inlines = [RigaOffertaInline]
    readonly_fields = ('numero', 'anno', 'imponibile', 'created_at', 'updated_at')


@admin.register(TestataOrdine)
class TestataOrdineAdmin(admin.ModelAdmin):
    list_display = (
        'numero', 'anno', 'data_documento', 'cliente', 'stato', 'imponibile',
    )
    list_filter = ('stato', 'anno')
    search_fields = ('numero', 'cliente__ragione_sociale')
    autocomplete_fields = ('cliente', 'fornitore', 'forme_pagamento', 'conto_corrente')
    date_hierarchy = 'data_documento'
    inlines = [RigaOrdineInline]
    readonly_fields = ('numero', 'anno', 'imponibile', 'created_at', 'updated_at')


@admin.register(TestataDdt)
class TestataDdtAdmin(admin.ModelAdmin):
    list_display = (
        'numero', 'anno', 'data_documento', 'cliente', 'stato',
        'numero_palette', 'peso_lordo',
    )
    list_filter = ('stato', 'anno')
    search_fields = ('numero', 'cliente__ragione_sociale')
    autocomplete_fields = (
        'cliente', 'fornitore', 'forme_pagamento', 'conto_corrente',
        'destinazione_merce',
    )
    date_hierarchy = 'data_documento'
    inlines = [RigaDdtInline]
    readonly_fields = ('numero', 'anno', 'imponibile', 'created_at', 'updated_at')


@admin.register(TestataFattura)
class TestataFatturaAdmin(admin.ModelAdmin):
    list_display = (
        'numero', 'anno', 'data_documento', 'cliente', 'tipo_documento',
        'imponibile', 'pagata', 'data_pagamento',
    )
    list_filter = ('tipo_documento', 'pagata', 'anno')
    search_fields = ('numero', 'cliente__ragione_sociale')
    autocomplete_fields = ('cliente', 'fornitore', 'forme_pagamento', 'conto_corrente')
    date_hierarchy = 'data_documento'
    inlines = [RigaFatturaInline, ScadenzaFatturaInline, AgenteFatturaInline]
    readonly_fields = ('numero', 'anno', 'imponibile', 'created_at', 'updated_at')


@admin.register(TestataNotaCredito)
class TestataNotaCreditoAdmin(admin.ModelAdmin):
    list_display = (
        'numero', 'anno', 'data_documento', 'cliente', 'tipo_documento', 'imponibile',
    )
    list_filter = ('tipo_documento', 'anno')
    search_fields = ('numero', 'cliente__ragione_sociale')
    autocomplete_fields = ('cliente', 'fornitore', 'forme_pagamento', 'conto_corrente')
    date_hierarchy = 'data_documento'
    inlines = [RigaNotaCreditoInline]
    readonly_fields = ('numero', 'anno', 'imponibile', 'created_at', 'updated_at')


# ---------------------------------------------------------------------------
# Ciclo passivo
# ---------------------------------------------------------------------------


@admin.register(TestataDdtFornitore)
class TestataDdtFornitoreAdmin(admin.ModelAdmin):
    list_display = (
        'numero', 'anno', 'data_documento', 'fornitore',
        'numero_palette', 'peso_lordo',
    )
    list_filter = ('anno',)
    search_fields = ('numero', 'fornitore__ragione_sociale')
    autocomplete_fields = ('cliente', 'fornitore', 'forme_pagamento', 'conto_corrente')
    date_hierarchy = 'data_documento'
    inlines = [RigaDdtFornitoreInline]
    readonly_fields = ('numero', 'anno', 'imponibile', 'created_at', 'updated_at')


class RigaFatturaAcquistoInline(admin.TabularInline):
    model = RigaFatturaAcquisto
    extra = 1
    autocomplete_fields = ('articolo', 'aliquota_iva', 'categoria_costo')


class ScadenzaFatturaAcquistoInline(admin.TabularInline):
    model = ScadenzaFatturaAcquisto
    extra = 0
    show_change_link = True


@admin.register(TestataFatturaAcquisto)
class TestataFatturaAcquistoAdmin(admin.ModelAdmin):
    list_display = (
        'numero', 'anno', 'fornitore', 'numero_fornitore', 'data_fornitore',
        'tipo_documento', 'stato', 'totale_documento', 'pagata',
    )
    list_filter = ('stato', 'tipo_documento', 'pagata', 'anno')
    search_fields = (
        'numero', 'numero_fornitore', 'fornitore__ragione_sociale',
    )
    autocomplete_fields = ('cliente', 'fornitore', 'forme_pagamento', 'conto_corrente')
    date_hierarchy = 'data_documento'
    inlines = [RigaFatturaAcquistoInline, ScadenzaFatturaAcquistoInline]
    readonly_fields = (
        'numero', 'anno', 'imponibile', 'imposta', 'totale_documento',
        'created_at', 'updated_at',
    )


class PagamentoScadenzaAcquistoInline(admin.TabularInline):
    model = PagamentoScadenzaAcquisto
    extra = 0


@admin.register(ScadenzaFatturaAcquisto)
class ScadenzaFatturaAcquistoAdmin(admin.ModelAdmin):
    list_display = (
        'fattura', 'data_scadenza', 'importo', 'modalita_pagamento', 'stato',
    )
    list_filter = ('modalita_pagamento',)
    search_fields = ('fattura__numero', 'fattura__fornitore__ragione_sociale')
    inlines = [PagamentoScadenzaAcquistoInline]


@admin.register(PagamentoScadenzaAcquisto)
class PagamentoScadenzaAcquistoAdmin(admin.ModelAdmin):
    list_display = ('scadenza', 'data_pagamento', 'importo', 'modalita_pagamento')
    list_filter = ('modalita_pagamento', 'data_pagamento')


# ---------------------------------------------------------------------------
# Righe e scadenze (visualizzazione diretta)
# ---------------------------------------------------------------------------


@admin.register(RigaDocumento)
class RigaDocumentoAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'numero_riga', 'articolo', 'descrizione_libera',
        'quantita', 'importo_unitario', 'iva',
    )
    search_fields = ('descrizione_libera', 'articolo__codice')
    autocomplete_fields = ('articolo', 'iva')


@admin.register(RigaFatturaAcquisto)
class RigaFatturaAcquistoAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'testata', 'numero_riga', 'articolo', 'descrizione',
        'quantita', 'prezzo_unitario', 'aliquota_iva', 'categoria_costo',
    )
    search_fields = ('descrizione', 'articolo__codice')
    autocomplete_fields = ('articolo', 'aliquota_iva', 'categoria_costo')


@admin.register(ScadenzaFattura)
class ScadenzaFatturaAdmin(admin.ModelAdmin):
    list_display = ('fattura', 'data', 'importo')
    date_hierarchy = 'data'


@admin.register(MessaggioSdi)
class MessaggioSdiAdmin(admin.ModelAdmin):
    list_display = ('fattura', 'tipo', 'direzione', 'timestamp', 'id_trasmissione', 'descrizione')
    list_filter = ('tipo', 'direzione', 'timestamp')
    search_fields = ('id_trasmissione', 'descrizione', 'fattura__numero')
    readonly_fields = ('timestamp',)
    date_hierarchy = 'timestamp'


@admin.register(AgenteFattura)
class AgenteFatturaAdmin(admin.ModelAdmin):
    list_display = ('agente', 'fattura', 'provvigione')
    autocomplete_fields = ('agente',)
