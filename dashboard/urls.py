from django.urls import path

from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.DashboardView.as_view(), name='home'),
    path('ui/theme/', views.set_theme, name='set_theme'),
    path('ui/density/', views.set_density, name='set_density'),
]
