from django.urls import path

from . import views

app_name = 'opportunita'

urlpatterns = [
    path('', views.PipelineView.as_view(), name='list'),
    path('nuova/', views.OpportunitaCreateView.as_view(), name='create'),
    path('<int:pk>/', views.OpportunitaDetailView.as_view(), name='detail'),
    path('<int:pk>/modifica/', views.OpportunitaUpdateView.as_view(), name='update'),
    path('<int:pk>/sposta/', views.sposta_stadio, name='sposta'),
    path('<int:pk>/elimina/', views.OpportunitaDeleteView.as_view(), name='delete'),
]
