from django.urls import path

from . import integrazioni, views

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
    path('integrazioni/', integrazioni.integrazioni, name='integrazioni'),
    path('fatturazione/', views.fatturazione, name='fatturazione'),
    path('documenti/', views.documenti, name='documenti'),
    path('documenti/anteprima/', views.documenti_anteprima, name='documenti_anteprima'),
    path('api/', views.api_tokens, name='api'),
    path('audit/', views.audit_log, name='audit'),
    path('backup/', views.backup, name='backup'),
    path('backup/<str:nome>/', views.backup_scarica, name='backup_scarica'),
]
