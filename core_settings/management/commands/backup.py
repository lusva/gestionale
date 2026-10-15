"""``python manage.py backup``: backup completo (database + file caricati).

Per farlo ogni notte: deploy/gestionale-backup.timer. Vedi core_settings/backup.py.
"""
from django.core.management.base import BaseCommand

from core_settings import backup


class Command(BaseCommand):
    help = 'Backup completo del gestionale (database SQLite + media) in backups/, con rotazione.'

    def add_arguments(self, parser):
        parser.add_argument('--senza-media', action='store_true',
                            help='Solo il database, senza i file caricati.')
        parser.add_argument('--conserva', type=int, default=backup.CONSERVA,
                            help=f'Quanti backup tenere (default {backup.CONSERVA}).')

    def handle(self, *args, **opts):
        file = backup.crea(con_media=not opts['senza_media'], conserva=opts['conserva'])
        self.stdout.write(self.style.SUCCESS(f'Backup creato: {file} ({file.stat().st_size // 1024} KB)'))
