from django.urls import path

from . import views


app_name = 'cashflow'


urlpatterns = [
    path('', views.CashflowView.as_view(), name='timeline'),

    # Scadenze fiscali
    path('fiscali/', views.ScadenzaFiscaleListView.as_view(), name='scadenza_fiscale_list'),
    path('fiscali/nuova/', views.ScadenzaFiscaleCreateView.as_view(), name='scadenza_fiscale_create'),
    path('fiscali/<int:pk>/modifica/', views.ScadenzaFiscaleUpdateView.as_view(), name='scadenza_fiscale_update'),
    path('fiscali/<int:pk>/elimina/', views.ScadenzaFiscaleDeleteView.as_view(), name='scadenza_fiscale_delete'),
    path('fiscali/<int:pk>/pagata/', views.scadenza_fiscale_marca_pagata, name='scadenza_fiscale_pagata'),

    # Spese ricorrenti
    path('spese/', views.SpesaRicorrenteListView.as_view(), name='spesa_ricorrente_list'),
    path('spese/nuova/', views.SpesaRicorrenteCreateView.as_view(), name='spesa_ricorrente_create'),
    path('spese/<int:pk>/', views.SpesaRicorrenteDetailView.as_view(), name='spesa_ricorrente_detail'),
    path('spese/<int:pk>/modifica/', views.SpesaRicorrenteUpdateView.as_view(), name='spesa_ricorrente_update'),
    path('spese/<int:pk>/elimina/', views.SpesaRicorrenteDeleteView.as_view(), name='spesa_ricorrente_delete'),
    path('spese/scadenza/<int:pk>/pagata/', views.scadenza_spesa_marca_pagata, name='scadenza_spesa_pagata'),

    # Rimborsi chilometrici
    path('rimborsi-km/', views.RimborsoChilometricoListView.as_view(), name='rimborso_km_list'),
    path('rimborsi-km/nuovo/', views.RimborsoChilometricoCreateView.as_view(), name='rimborso_km_create'),
    path('rimborsi-km/<int:pk>/modifica/', views.RimborsoChilometricoUpdateView.as_view(), name='rimborso_km_update'),
    path('rimborsi-km/<int:pk>/elimina/', views.RimborsoChilometricoDeleteView.as_view(), name='rimborso_km_delete'),
    path('rimborsi-km/<int:pk>/pagato/', views.rimborso_km_marca_pagato, name='rimborso_km_pagato'),
]
