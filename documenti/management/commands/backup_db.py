"""
Command: ``python manage.py backup_db``

Crea uno snapshot del database in formato JSON (``dumpdata``) compresso,
con rotazione automatica dei file vecchi.

Argomenti:
- ``--dir`` (default ``backups/``): cartella di destinazione (relativa a BASE_DIR)
- ``--keep`` (default 14): numero di file da mantenere (rotation FIFO)
- ``--exclude`` (default contenttypes,auth.permission,sessions.session,admin.logentry):
  app/modelli da escludere (separati da virgola)

Output: ``backups/gestionale_<timestamp>.json.gz`` + lista file presenti.
"""
from __future__ import annotations

import gzip
import os
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Esporta lo stato del DB in JSON compresso, con rotazione.'

    def add_arguments(self, parser):
        parser.add_argument('--dir', default='backups',
                            help='Cartella destinazione (rel. a BASE_DIR).')
        parser.add_argument('--keep', type=int, default=14,
                            help='Numero file da conservare (rotazione).')
        parser.add_argument('--exclude', default=(
            'contenttypes,auth.permission,sessions.session,admin.logentry'
        ), help='App/modelli da escludere.')

    def handle(self, *args, **opts):
        out_dir = Path(settings.BASE_DIR) / opts['dir']
        out_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        target = out_dir / f'gestionale_{ts}.json.gz'

        excludes = [
            e.strip() for e in opts['exclude'].split(',') if e.strip()
        ]
        # ``dumpdata`` scrive su stdout: cattura in buffer e comprimi.
        from io import StringIO
        buffer = StringIO()
        call_command(
            'dumpdata',
            *[f'--exclude={e}' for e in excludes],
            indent=2,
            natural_foreign=True,
            natural_primary=True,
            stdout=buffer,
        )
        data = buffer.getvalue().encode('utf-8')

        with gzip.open(target, 'wb') as f:
            f.write(data)

        size_kb = target.stat().st_size / 1024
        self.stdout.write(self.style.SUCCESS(
            f'Snapshot creato: {target.name} ({size_kb:.1f} KB)'
        ))

        # Rotation: tieni solo gli ultimi ``keep`` file
        backups = sorted(
            out_dir.glob('gestionale_*.json.gz'),
            key=lambda p: p.stat().st_mtime,
        )
        keep = opts['keep']
        if len(backups) > keep:
            for old in backups[:-keep]:
                old.unlink()
                self.stdout.write(f'  rotated out: {old.name}')

        self.stdout.write('')
        n_remaining = len(list(out_dir.glob('gestionale_*.json.gz')))
        self.stdout.write(f'Backup totali in {out_dir}: {n_remaining}')
