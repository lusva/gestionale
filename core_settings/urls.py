from django.urls import path

from . import views

app_name = 'settings'

urlpatterns = [
    path('', views.utenti, name='home'),
    path('organizzazione/', views.organizzazione, name='organizzazione'),
    path('utenti/', views.utenti, name='utenti'),
    path('utenti/<int:pk>/ruolo/', views.cambia_ruolo, name='utente_cambia_ruolo'),
    path('utenti/<int:pk>/toggle-attivo/', views.utente_toggle_attivo, name='utente_toggle_attivo'),
    path('utenti/<int:pk>/elimina/', views.utente_elimina, name='utente_elimina'),
    path('utenti/invita/', views.invita_utente, name='utente_invita'),
    path('ruoli/', views.ruoli, name='ruoli'),
    path('integrazioni/', views.placeholder, {'titolo': 'Integrazioni'}, name='integrazioni'),
    path('fatturazione/', views.placeholder, {'titolo': 'Fatturazione'}, name='fatturazione'),
    path('api/', views.api_tokens, name='api'),
    path('audit/', views.audit_log, name='audit'),
    path('backup/', views.placeholder, {'titolo': 'Backup'}, name='backup'),
]
