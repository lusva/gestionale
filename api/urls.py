from django.urls import path

from . import views

app_name = 'api'

urlpatterns = [
    path('v1/health',             views.health,             name='health'),
    path('v1/clienti',            views.clienti_list,       name='clienti'),
    path('v1/clienti/<int:pk>',   views.cliente_detail,     name='cliente'),
    path('v1/opportunita',        views.opportunita_list,   name='opportunita'),
    path('v1/opportunita/<int:pk>', views.opportunita_detail, name='opportunita_detail'),
    path('v1/attivita',           views.attivita_list,      name='attivita'),
]
