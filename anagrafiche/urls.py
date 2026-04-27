from django.urls import path

from . import import_csv, views


app_name = 'anagrafiche'


def _crud_paths(prefix, plural_url, view_classes):
    L, D, C, U, X = view_classes
    return [
        path(f'{plural_url}/', L.as_view(), name=f'{prefix}_list'),
        path(f'{plural_url}/nuovo/', C.as_view(), name=f'{prefix}_create'),
        path(f'{plural_url}/<int:pk>/', D.as_view(), name=f'{prefix}_detail'),
        path(f'{plural_url}/<int:pk>/modifica/', U.as_view(), name=f'{prefix}_update'),
        path(f'{plural_url}/<int:pk>/elimina/', X.as_view(), name=f'{prefix}_delete'),
    ]


urlpatterns = [
    # Anagrafica azienda (singleton)
    path('azienda/', views.anagrafica_azienda_edit, name='anagrafica_azienda'),

    # Import CSV
    path('articoli/import/', import_csv.articoli_import, name='articoli_import'),
    path('fornitori/import/', import_csv.fornitori_import, name='fornitori_import'),

    # Forme di pagamento (con scadenze inline)
    path('forme-pagamento/', views.forme_pagamento_list, name='forme_pagamento_list'),
    path('forme-pagamento/nuova/', views.forma_pagamento_create, name='forma_pagamento_create'),
    path('forme-pagamento/<int:pk>/modifica/', views.forma_pagamento_update, name='forma_pagamento_update'),
    path('forme-pagamento/<int:pk>/elimina/', views.forma_pagamento_delete, name='forma_pagamento_delete'),
]


_ANAG = [
    ('fornitore', 'fornitori', (
        views.FornitoreListView, views.FornitoreDetailView,
        views.FornitoreCreateView, views.FornitoreUpdateView, views.FornitoreDeleteView,
    )),
    ('articolo', 'articoli', (
        views.ArticoloListView, views.ArticoloDetailView,
        views.ArticoloCreateView, views.ArticoloUpdateView, views.ArticoloDeleteView,
    )),
    ('posizione_iva', 'posizioni-iva', (
        views.PosizioneIvaListView, views.PosizioneIvaDetailView,
        views.PosizioneIvaCreateView, views.PosizioneIvaUpdateView, views.PosizioneIvaDeleteView,
    )),
    ('categoria_costo', 'categorie-costo', (
        views.CategoriaCostoListView, views.CategoriaCostoDetailView,
        views.CategoriaCostoCreateView, views.CategoriaCostoUpdateView, views.CategoriaCostoDeleteView,
    )),
]
for prefix, plural, vs in _ANAG:
    urlpatterns += _crud_paths(prefix, plural, vs)
