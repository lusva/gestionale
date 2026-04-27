from django.urls import path

from . import analytics, dashboard, doc_views, export_csv, views, workflow


app_name = 'documenti'


def _crud_paths(prefix, doc_views_tuple, plural_url):
    """Helper: ritorna 5 path per List/Create/Detail/Update/Delete."""
    L, D, C, U, X = doc_views_tuple
    return [
        path(f'{plural_url}/', L.as_view(), name=f'{prefix}_list'),
        path(f'{plural_url}/nuova/', C.as_view(), name=f'{prefix}_create'),
        path(f'{plural_url}/<int:pk>/', D.as_view(), name=f'{prefix}_detail'),
        path(f'{plural_url}/<int:pk>/modifica/', U.as_view(), name=f'{prefix}_update'),
        path(f'{plural_url}/<int:pk>/elimina/', X.as_view(), name=f'{prefix}_delete'),
    ]


urlpatterns = [
    # Dashboard documenti
    path('dashboard/', dashboard.dashboard, name='dashboard'),

    # Export CSV
    path('fatture/export/', export_csv.fatture_export, name='fatture_export'),
    path('scadenziario-attivo/export/', export_csv.scadenziario_attivo_export, name='scadenziario_attivo_export'),
    path('scadenziario-passivo/export/', export_csv.scadenziario_passivo_export, name='scadenziario_passivo_export'),
    path('costi-categoria/export/', export_csv.costi_per_categoria_export, name='costi_per_categoria_export'),

    # Analytics
    path('scadenziario-attivo/', analytics.scadenziario_attivo, name='scadenziario_attivo'),
    path('scadenziario-passivo/', analytics.scadenziario_passivo, name='scadenziario_passivo'),
    path('costi-categoria/', analytics.costi_per_categoria, name='costi_per_categoria'),
    path('vendite-cliente/', analytics.vendite_per_cliente, name='vendite_per_cliente'),
    path('provvigioni-agenti/', analytics.provvigioni_agenti, name='provvigioni_agenti'),
    path('margine-articoli/', analytics.margine_articoli, name='margine_articoli'),
    path('top-articoli/', analytics.top_articoli, name='top_articoli'),

    # Fatture cliente
    path('fatture/', views.FatturaListView.as_view(), name='fattura_list'),
    path('fatture/nuova/', views.FatturaCreateView.as_view(), name='fattura_create'),
    path('fatture/<int:pk>/', views.FatturaDetailView.as_view(), name='fattura_detail'),
    path('fatture/<int:pk>/modifica/', views.FatturaUpdateView.as_view(), name='fattura_update'),
    path('fatture/<int:pk>/elimina/', views.FatturaDeleteView.as_view(), name='fattura_delete'),
    path('fatture/<int:pk>/toggle-pagata/', views.fattura_toggle_pagata, name='fattura_toggle_pagata'),
    path('fatture/<int:pk>/pdf/', views.fattura_pdf, name='fattura_pdf'),
    path('fatture/zip/', views.fatture_zip, name='fatture_zip'),
    path('fatture/<int:pk>/pdf-firmato/', views.fattura_pdf_firmato, name='fattura_pdf_firmato'),
    path('fatture/<int:pk>/xml/', views.fattura_xml_elettronica, name='fattura_xml'),
    path('fatture/<int:pk>/invia-email/', views.fattura_invia_email, name='fattura_invia_email'),
    path('fatture/<int:pk>/invia-sdi/', views.fattura_invia_sdi, name='fattura_invia_sdi'),

    # Fatture acquisto (ciclo passivo)
    path('fatture-acquisto/', views.FatturaAcquistoListView.as_view(), name='fattura_acquisto_list'),
    path('fatture-acquisto/<int:pk>/', views.FatturaAcquistoDetailView.as_view(), name='fattura_acquisto_detail'),
    path('fatture-acquisto/<int:pk>/cambia-stato/', views.fattura_acquisto_cambia_stato, name='fattura_acquisto_cambia_stato'),
    path('fatture-acquisto/import-xml/', views.fattura_acquisto_import_xml, name='fattura_acquisto_import_xml'),
    path('scadenze-acquisto/<int:scadenza_id>/pagamenti/nuovo/', views.pagamento_scadenza_acquisto_create, name='pagamento_acquisto_create'),

    # Workflow transizioni
    path('offerte/<int:pk>/conferma-ordine/', workflow.offerta_to_ordine, name='offerta_to_ordine'),
    path('ordini/<int:pk>/genera-ddt/', workflow.ordine_to_ddt, name='ordine_to_ddt'),
    path('ordini/<int:pk>/genera-fattura/', workflow.ordine_to_fattura, name='ordine_to_fattura'),
    path('fatture/<int:pk>/nota-credito/', workflow.fattura_to_nota_credito, name='fattura_to_nota_credito'),

    # PDF altri documenti
    path('offerte/<int:pk>/pdf/', views.offerta_pdf, name='offerta_pdf'),
    path('ordini/<int:pk>/pdf/', views.ordine_pdf, name='ordine_pdf'),
    path('ddt/<int:pk>/pdf/', views.ddt_pdf, name='ddt_pdf'),
    path('note-credito/<int:pk>/pdf/', views.nota_credito_pdf, name='nota_credito_pdf'),

    # HTMX: form-row vuoto per aggiungere riga
    path('fatture/riga/empty/', views.fattura_riga_form_empty, name='fattura_riga_empty'),
]


# CRUD generico per tutti gli altri tipi documento
_DOCS = [
    ('offerta',  (
        doc_views.OffertaListView, doc_views.OffertaDetailView,
        doc_views.OffertaCreateView, doc_views.OffertaUpdateView,
        doc_views.OffertaDeleteView,
    ), 'offerte'),
    ('ordine',   (
        doc_views.OrdineListView, doc_views.OrdineDetailView,
        doc_views.OrdineCreateView, doc_views.OrdineUpdateView,
        doc_views.OrdineDeleteView,
    ), 'ordini'),
    ('ddt',      (
        doc_views.DdtListView, doc_views.DdtDetailView,
        doc_views.DdtCreateView, doc_views.DdtUpdateView,
        doc_views.DdtDeleteView,
    ), 'ddt'),
    ('nota_credito', (
        doc_views.NotaCreditoListView, doc_views.NotaCreditoDetailView,
        doc_views.NotaCreditoCreateView, doc_views.NotaCreditoUpdateView,
        doc_views.NotaCreditoDeleteView,
    ), 'note-credito'),
    ('ddt_fornitore', (
        doc_views.DdtFornitoreListView, doc_views.DdtFornitoreDetailView,
        doc_views.DdtFornitoreCreateView, doc_views.DdtFornitoreUpdateView,
        doc_views.DdtFornitoreDeleteView,
    ), 'ddt-fornitore'),
]

for prefix, views_tuple, plural in _DOCS:
    urlpatterns += _crud_paths(prefix, views_tuple, plural)
