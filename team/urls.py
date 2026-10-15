from django.urls import path

from . import views

app_name = 'team'

urlpatterns = [
    path('', views.index, name='index'),
    path('<int:pk>/', views.scheda, name='scheda'),
    path('<int:pk>/modifica/', views.modifica, name='modifica'),
]
