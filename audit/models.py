from django.conf import settings
from django.db import models


class Azione(models.TextChoices):
    CREATE = 'create', 'Creato'
    UPDATE = 'update', 'Modificato'
    DELETE = 'delete', 'Eliminato'
    LOGIN = 'login', 'Login'
    LOGOUT = 'logout', 'Logout'
    LOGIN_FAILED = 'login_failed', 'Login fallito'
    IMPORT = 'import', 'Import'
    EXPORT = 'export', 'Export'


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='audit_logs',
    )
    actor_label = models.CharField(
        max_length=150, blank=True,
        help_text='Etichetta (email/username) salvata anche dopo cancellazione utente',
    )
    azione = models.CharField(max_length=20, choices=Azione.choices)
    target_type = models.CharField(
        max_length=50, blank=True,
        help_text='Es. "Cliente", "Opportunita"',
    )
    target_id = models.CharField(max_length=50, blank=True)
    target_label = models.CharField(
        max_length=200, blank=True,
        help_text='Label leggibile del target al momento dell\'evento',
    )
    ip = models.GenericIPAddressField(null=True, blank=True)
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['azione']),
            models.Index(fields=['target_type']),
        ]

    def __str__(self):
        return f'[{self.azione}] {self.target_type} #{self.target_id} — {self.actor_label}'
