"""Upgrade regression: preserve primary keys and every existing relation."""
from datetime import date

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ExistingCompanyMigrationTests(TransactionTestCase):
    migrated_apps = (
        'accounts', 'anagrafiche', 'api', 'attivita', 'audit', 'cashflow',
        'clienti', 'core_settings', 'documenti', 'magazzino', 'opportunita', 'tenancy',
    )

    def test_upgrade_keeps_existing_records_and_referente_primary_keys(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        # Resolve original migration names from the first tenancy migrations'
        # own dependencies, so this fixture documents the actual upgrade edge.
        before = []
        for app in self.migrated_apps:
            if app == 'tenancy':
                before.append((app, None))
                continue
            new = next(
                migration for (label, _), migration in executor.loader.disk_migrations.items()
                if label == app and ('tenancy', '0001_initial') in migration.dependencies
            )
            before.append(next(dep for dep in new.dependencies if dep[0] == app))
        try:
            executor.migrate(before)
            old = executor.loader.project_state([node for node in before if node[1]]).apps
            ref = old.get_model('anagrafiche', 'Referente').objects.create(numero=37, nome='Referente storico')
            cliente = old.get_model('clienti', 'Cliente').objects.create(
                ragione_sociale='Cliente storico', referente_id=ref.pk,
            )
            doc = old.get_model('documenti', 'TestataOfferta').objects.create(
                cliente_id=cliente.pk, numero=8, anno=2025, data_documento=date(2025, 4, 1),
            )
            user = old.get_model('auth', 'User').objects.create(username='utente-storico')
            old.get_model('accounts', 'Profile').objects.create(user_id=user.pk)

            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            after = executor.loader.project_state(latest).apps
            ref_after = after.get_model('anagrafiche', 'Referente').objects.get(pk=37)
            self.assertEqual((ref_after.numero, ref_after.azienda_id), (37, 1))
            cliente_after = after.get_model('clienti', 'Cliente').objects.get(pk=cliente.pk)
            self.assertEqual((cliente_after.referente_id, cliente_after.azienda_id), (37, 1))
            doc_after = after.get_model('documenti', 'TestataOfferta').objects.get(pk=doc.pk)
            self.assertEqual((doc_after.cliente_id, doc_after.numero, doc_after.azienda_id), (cliente.pk, 8, 1))
            self.assertEqual(after.get_model('accounts', 'Profile').objects.get(user_id=user.pk).azienda_id, 1)
            self.assertEqual(after.get_model('tenancy', 'Azienda').objects.count(), 1)
            connection.check_constraints()
        finally:
            MigrationExecutor(connection).migrate(latest)
