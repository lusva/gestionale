"""Cancella le risposte idempotenti scadute (``Idempotency-Key`` / ``chiave``
dell'MCP). Da lanciare ogni giorno da cron:

    python manage.py pulisci_idempotenza
"""
from django.core.management.base import BaseCommand

from api.models import RichiestaIdempotente


class Command(BaseCommand):
    help = 'Cancella le richieste idempotenti più vecchie di IDEMPOTENZA_ORE (24 h).'

    def handle(self, *args, **options):
        n = RichiestaIdempotente.pulisci()
        self.stdout.write(self.style.SUCCESS(f'Richieste idempotenti cancellate: {n}'))
