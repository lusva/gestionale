from django.urls import path

from . import views

app_name = 'accounts'

urlpatterns = [
    path('configura-azienda/', views.configura_azienda, name='configura_azienda'),
    path('login/', views.LoginView.as_view(), name='login'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
]
