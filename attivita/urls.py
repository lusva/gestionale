from django.urls import path

from . import views

app_name = 'attivita'

urlpatterns = [
    path('', views.AttivitaListView.as_view(), name='list'),
    path('nuova/', views.AttivitaCreateView.as_view(), name='create'),
    path('<int:pk>/', views.AttivitaDetailView.as_view(), name='detail'),
    path('<int:pk>/modifica/', views.AttivitaUpdateView.as_view(), name='update'),
    path('<int:pk>/toggle/', views.toggle_completata, name='toggle'),
    path('<int:pk>/elimina/', views.AttivitaDeleteView.as_view(), name='delete'),
]
