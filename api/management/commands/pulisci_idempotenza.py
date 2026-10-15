"""Cancella le risposte idempotenti scadute (``Idempotency-Key`` / ``chiave``
dell'MCP). Da lanciare ogni giorno da cron:

    python manage.py pulisci_idempotenza
"""
from tenancy.commands import AziendeCommand

from api.models import RichiestaIdempotente


class Command(AziendeCommand):
    help = 'Cancella le richieste idempotenti più vecchie di IDEMPOTENZA_ORE (24 h).'

    def handle_azienda(self, *args, **options):
        n = RichiestaIdempotente.pulisci()
        self.stdout.write(self.style.SUCCESS(f'Richieste idempotenti cancellate: {n}'))
