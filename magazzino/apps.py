from django.apps import AppConfig


class MagazzinoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'magazzino'
    verbose_name = 'Magazzino'

    def ready(self):
        # Collega i signal per auto-movimento da RigaDocumento
        from . import signals  # noqa: F401
