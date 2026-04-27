"""
Viste CRUD per Offerta, Ordine, DDT, NotaCredito, DdtFornitore.

Sono istanziate dal factory ``make_doc_views``: ognuna ottiene 5 CBV
(List/Detail/Create/Update/Delete) parametrizzate sul modello e i form
specifici.
"""
from .doc_forms import (
    RigaDdtFormSet,
    RigaDdtFornitoreFormSet,
    RigaNotaCreditoFormSet,
    RigaOffertaFormSet,
    RigaOrdineFormSet,
    TestataDdtForm,
    TestataDdtFornitoreForm,
    TestataNotaCreditoForm,
    TestataOffertaForm,
    TestataOrdineForm,
)
from .doc_generic import make_doc_views
from .models import (
    TestataDdt,
    TestataDdtFornitore,
    TestataNotaCredito,
    TestataOfferta,
    TestataOrdine,
)


# Offerte
(OffertaListView, OffertaDetailView, OffertaCreateView,
 OffertaUpdateView, OffertaDeleteView) = make_doc_views(
    model=TestataOfferta,
    form_class=TestataOffertaForm,
    riga_formset_class=RigaOffertaFormSet,
    label='Offerta',
    label_plural='Offerte',
    active_nav='offerte',
    url_prefix='offerta',
    counterparty='cliente',
    has_stato=True,
)


# Ordini
(OrdineListView, OrdineDetailView, OrdineCreateView,
 OrdineUpdateView, OrdineDeleteView) = make_doc_views(
    model=TestataOrdine,
    form_class=TestataOrdineForm,
    riga_formset_class=RigaOrdineFormSet,
    label='Ordine',
    label_plural='Ordini',
    active_nav='ordini',
    url_prefix='ordine',
    counterparty='cliente',
    has_stato=True,
)


# DDT cliente
(DdtListView, DdtDetailView, DdtCreateView,
 DdtUpdateView, DdtDeleteView) = make_doc_views(
    model=TestataDdt,
    form_class=TestataDdtForm,
    riga_formset_class=RigaDdtFormSet,
    label='DDT',
    label_plural='DDT',
    active_nav='ddt',
    url_prefix='ddt',
    counterparty='cliente',
    has_stato=True,
)


# Note di credito
(NotaCreditoListView, NotaCreditoDetailView, NotaCreditoCreateView,
 NotaCreditoUpdateView, NotaCreditoDeleteView) = make_doc_views(
    model=TestataNotaCredito,
    form_class=TestataNotaCreditoForm,
    riga_formset_class=RigaNotaCreditoFormSet,
    label='Nota di credito',
    label_plural='Note di credito',
    active_nav='note_credito',
    url_prefix='nota_credito',
    counterparty='cliente',
    has_stato=False,
)


# DDT fornitore (ciclo passivo)
(DdtFornitoreListView, DdtFornitoreDetailView, DdtFornitoreCreateView,
 DdtFornitoreUpdateView, DdtFornitoreDeleteView) = make_doc_views(
    model=TestataDdtFornitore,
    form_class=TestataDdtFornitoreForm,
    riga_formset_class=RigaDdtFornitoreFormSet,
    label='DDT fornitore',
    label_plural='DDT fornitore',
    active_nav='ddt_fornitore',
    url_prefix='ddt_fornitore',
    counterparty='fornitore',
    has_stato=False,
)
