from django.apps import AppConfig


class DocumentiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'documenti'
    verbose_name = 'Documenti (ciclo attivo e passivo)'

    def ready(self):
        # Carica i signal di audit (post_save/post_delete sulle testate)
        from . import audit_signals  # noqa: F401
