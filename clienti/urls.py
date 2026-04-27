from django.urls import path

from . import views

app_name = 'clienti'

urlpatterns = [
    path('', views.ClienteListView.as_view(), name='list'),
    path('nuovo/', views.ClienteCreateView.as_view(), name='create'),
    path('<int:pk>/', views.ClienteDetailView.as_view(), name='detail'),
    path('<int:pk>/modifica/', views.ClienteUpdateView.as_view(), name='update'),
    path('<int:pk>/elimina/', views.ClienteDeleteView.as_view(), name='delete'),

    path('partial/rows/', views.ClienteRowsPartial.as_view(), name='rows_partial'),
    path('valida-piva/', views.valida_partita_iva, name='valida_piva'),
    path('import-csv/', views.import_csv, name='import_csv'),
    path('export-csv/', views.export_csv, name='export_csv'),

    path('contatti/', views.ContattoListView.as_view(), name='contatti_list'),
    path('<int:cliente_pk>/contatti/nuovo/', views.ContattoCreateView.as_view(), name='contatto_create'),
    path('contatti/<int:pk>/elimina/', views.ContattoDeleteView.as_view(), name='contatto_delete'),
]
