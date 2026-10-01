from django.urls import path

from . import v2, views

app_name = 'api'

urlpatterns = [
    path('v1/health',             views.health,             name='health'),
    path('v1/clienti',            views.clienti_list,       name='clienti'),
    path('v1/clienti/<int:pk>',   views.cliente_detail,     name='cliente'),
    path('v1/opportunita',        views.opportunita_list,   name='opportunita'),
    path('v1/opportunita/<int:pk>', views.opportunita_detail, name='opportunita_detail'),
    path('v1/attivita',           views.attivita_list,      name='attivita'),

    path('v2/health',               v2.health,          name='v2_health'),
    path('v2/clienti',              v2.clienti,         name='v2_clienti'),
    path('v2/clienti/<int:pk>',     v2.cliente,         name='v2_cliente'),
    path('v2/posizioni-iva',        v2.posizioni_iva,   name='v2_posizioni_iva'),
    path('v2/forme-pagamento',      v2.forme_pagamento, name='v2_forme_pagamento'),
    path('v2/conti-correnti',       v2.conti_correnti,  name='v2_conti_correnti'),
    path('v2/fatture',              v2.fatture,         name='v2_fatture'),
    path('v2/fatture/<int:pk>',     v2.fattura,         name='v2_fattura'),
]
