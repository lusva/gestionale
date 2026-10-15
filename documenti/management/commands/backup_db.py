"""
Command: ``python manage.py backup_db``

Crea uno snapshot completo di tutte le aziende in formato JSON compresso,
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
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.apps import apps
from django.core import serializers
from django.core.management.utils import parse_apps_and_model_labels
from django.core.management.base import BaseCommand
from django.db import router
from tenancy.context import azienda_context


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
        excluded_models, excluded_apps = parse_apps_and_model_labels(excludes)
        app_list = [(app, None) for app in apps.get_app_configs()
                    if app.models_module is not None and app not in excluded_apps]

        def objects():
            # Questo è un comando riservato al server: il backup comprende
            # esplicitamente tutte le aziende. dumpdata userebbe invece il
            # manager limitato all'azienda corrente, producendo una copia parziale.
            for model in serializers.sort_dependencies(app_list, allow_cycles=True):
                if model in excluded_models or model._meta.proxy:
                    continue
                if not router.allow_migrate_model('default', model):
                    continue
                manager = getattr(model, 'all_objects', model._base_manager)
                for obj in manager.order_by(model._meta.pk.name).iterator():
                    # La serializzazione delle relazioni M2M deve poter leggere
                    # quelle dell'oggetto, anche quando appartiene a un'altra azienda.
                    with azienda_context(getattr(obj, 'azienda_id', None)):
                        yield obj

        partial = target.with_suffix('.gz.tmp')
        try:
            with gzip.open(partial, 'wt', encoding='utf-8') as output:
                serializers.serialize('json', objects(), indent=2,
                                      use_natural_foreign_keys=True,
                                      use_natural_primary_keys=True, stream=output)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)

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
