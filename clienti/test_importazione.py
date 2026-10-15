import json
from unittest.mock import patch

from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from audit.models import AuditLog

from .importazione import esegui_piano, prepara_import
from .models import Cliente, Settore


class ImportazionePianoTests(TestCase):
    headers = ['ragione_sociale', 'partita_iva', 'settore', 'pec', 'citta', 'tipo', 'stato', 'nazione', 'note']

    @classmethod
    def setUpTestData(cls):
        # La migrazione inizializza settori predefiniti; questi test costruiscono
        # cataloghi espliciti per verificare creazioni e collisioni.
        Settore.objects.all().delete()

    def prepare(self, rows, *, headers=None, mapping=None, lock=False):
        headers = headers or self.headers
        return prepara_import(headers, rows, mapping or {field: field for field in headers}, lock=lock)

    def row(self, name='Alfa', vat='12345678901', **fields):
        values = {'ragione_sociale': name, 'partita_iva': vat, **fields}
        return [values.get(field, '') for field in self.headers]

    def test_preview_has_no_customer_sector_or_audit_writes(self):
        before = (Cliente.objects.count(), Settore.objects.count(), AuditLog.objects.count())
        with CaptureQueriesContext(connection) as queries:
            plan = self.prepare([self.row(settore='Nuovo settore')])
        self.assertEqual(plan['counts'], {'create': 1, 'update': 0, 'unchanged': 0, 'error': 0})
        self.assertEqual(before, (Cliente.objects.count(), Settore.objects.count(), AuditLog.objects.count()))
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for query in queries))

    def test_json_plan_creates_customer_and_sector_only_on_confirmation(self):
        plan = self.prepare([self.row(settore='Tecnologia', pec='alfa@pec.it', stato='ATTIVO')])
        plan = json.loads(json.dumps(plan))
        counts = esegui_piano(plan)
        self.assertEqual(counts, plan['counts'])
        customer = Cliente.objects.get()
        self.assertEqual(customer.partita_iva, 'IT12345678901')
        self.assertEqual(customer.stato, 'attivo')
        self.assertEqual(customer.settore.nome, 'Tecnologia')
        self.assertEqual(customer.pec, 'alfa@pec.it')

    def test_update_shows_exact_changes_and_preserves_blank_cells(self):
        sector = Settore.objects.create(nome='Esistente')
        customer = Cliente.objects.create(ragione_sociale='Prima', partita_iva='IT12345678901',
                                         pec='prima@pec.it', citta='Roma', note='Da conservare', settore=sector)
        plan = self.prepare([self.row(name='Dopo', citta='Milano')])
        row = plan['rows'][0]
        self.assertEqual(row['action'], 'update')
        self.assertEqual(row['changes'], [
            {'field': 'ragione_sociale', 'label': 'Ragione sociale', 'before': 'Prima', 'after': 'Dopo'},
            {'field': 'citta', 'label': 'Città', 'before': 'Roma', 'after': 'Milano'},
        ])
        esegui_piano(plan)
        customer.refresh_from_db()
        self.assertEqual(customer.ragione_sociale, 'Dopo')
        self.assertEqual(customer.citta, 'Milano')
        self.assertEqual(customer.pec, 'prima@pec.it')
        self.assertEqual(customer.note, 'Da conservare')
        self.assertEqual(customer.settore_id, sector.pk)

    def test_unchanged_has_no_save_timestamp_change_or_audit(self):
        customer = Cliente.objects.create(ragione_sociale='Alfa', partita_iva='12345678901', pec='alfa@pec.it')
        initial_updated = customer.updated_at
        initial_audit_count = AuditLog.objects.count()
        plan = self.prepare([self.row(vat='IT 123.456.789-01', pec='alfa@pec.it')])
        self.assertEqual(plan['rows'][0]['action'], 'unchanged')
        self.assertEqual(plan['rows'][0]['changes'], [])
        with patch.object(Cliente, 'save', side_effect=AssertionError('Unchanged must not save')):
            self.assertEqual(esegui_piano(plan)['unchanged'], 1)
        customer.refresh_from_db()
        self.assertEqual(customer.updated_at, initial_updated)
        self.assertEqual(customer.partita_iva, '12345678901')
        self.assertEqual(AuditLog.objects.count(), initial_audit_count)

    def test_existing_vat_normalizes_historical_case_and_separators_for_lookup(self):
        customer = Cliente.objects.create(ragione_sociale='Prima', partita_iva='it 123.45678901')
        plan = self.prepare([self.row(name='Dopo')])
        self.assertEqual(plan['rows'][0]['_customer_id'], customer.pk)
        self.assertEqual(plan['rows'][0]['action'], 'update')
        esegui_piano(plan)
        self.assertEqual(Cliente.objects.count(), 1)
        customer.refresh_from_db()
        self.assertEqual(customer.partita_iva, 'it 123.45678901')
        self.assertEqual(customer.ragione_sociale, 'Dopo')

    def test_all_duplicate_vat_occurrences_are_excluded(self):
        plan = self.prepare([self.row(name='Prima'), self.row(name='Seconda', vat='IT12345678901'),
                             self.row(name='Terza', vat='it 123.456.789-01')])
        self.assertEqual(plan['counts']['error'], 3)
        for row in plan['rows']:
            self.assertEqual(row['action'], 'error')
            self.assertIn('duplicata nel file', row['error'])
        esegui_piano(plan)
        self.assertFalse(Cliente.objects.exists())

    def test_invalid_duplicate_does_not_allow_the_other_occurrence(self):
        plan = self.prepare([self.row(name=''), self.row(name='Valida')])
        self.assertEqual(plan['counts']['error'], 2)
        self.assertTrue(all('duplicata nel file' in row['error'] for row in plan['rows']))

    def test_multiple_existing_matches_are_reported_without_writes(self):
        Cliente.objects.create(ragione_sociale='Storica', partita_iva='12345678901')
        Cliente.objects.create(ragione_sociale='Canonica', partita_iva='IT12345678901')
        plan = self.prepare([self.row()])
        self.assertEqual(plan['rows'][0]['action'], 'error')
        self.assertIn('più clienti', plan['rows'][0]['error'])
        esegui_piano(plan)
        self.assertEqual(set(Cliente.objects.values_list('ragione_sociale', flat=True)), {'Storica', 'Canonica'})

    def test_invalid_email_length_and_choices_are_errors_without_truncation(self):
        cases = [
            {'pec': 'non-una-mail'}, {'citta': 'C' * 101}, {'tipo': 'fornitore'},
            {'stato': 'sconosciuto'}, {'nazione': 'Italia'}, {'settore': 'S' * 101},
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                plan = self.prepare([self.row(**fields)])
                self.assertEqual(plan['counts']['error'], 1)
                self.assertTrue(plan['rows'][0]['error'])
                esegui_piano(plan)
        plan = self.prepare([self.row(name='R' * 201)])
        self.assertEqual(plan['rows'][0]['name'], 'R' * 201)
        self.assertEqual(plan['rows'][0]['action'], 'error')
        self.assertFalse(Cliente.objects.exists())
        self.assertFalse(Settore.objects.exists())

    def test_malformed_rows_include_csv_line_numbers_and_are_not_saved(self):
        rows = [self.row()[:-1], self.row(vat='22345678901') + ['extra'], self.row(vat='32345678901')]
        plan = self.prepare(rows)
        self.assertEqual([row['line'] for row in plan['rows']], [2, 3, 4])
        self.assertEqual([row['action'] for row in plan['rows']], ['error', 'error', 'create'])
        self.assertIn('Numero di colonne', plan['rows'][0]['error'])
        counts = esegui_piano(plan)
        self.assertEqual(counts['error'], 2)
        self.assertEqual(Cliente.objects.get().partita_iva, 'IT32345678901')

    def test_duplicate_source_headers_and_duplicate_target_mapping_are_rejected(self):
        plan = prepara_import(['nome', 'nome', 'piva'], [['A', 'B', '12345678901']],
                              {'nome': 'ragione_sociale', 'piva': 'partita_iva'})
        self.assertIn('intestazioni', plan['rows'][0]['error'])
        plan = prepara_import(['uno', 'due', 'piva'], [['A', 'B', '12345678901']],
                              {'uno': 'ragione_sociale', 'due': 'ragione_sociale', 'piva': 'partita_iva'})
        self.assertIn('più colonne', plan['rows'][0]['error'])
        self.assertEqual(plan['counts']['error'], 1)

    def test_mapping_can_ignore_columns_and_reorder_source_fields(self):
        headers = ['Da ignorare', 'VAT', 'Denominazione', 'Email PEC']
        plan = self.prepare([['qualsiasi', '12345678901', 'Alfa', 'alfa@pec.it']], headers=headers,
                            mapping={'VAT': 'partita_iva', 'Denominazione': 'ragione_sociale', 'Email PEC': 'pec'})
        self.assertEqual(plan['counts']['create'], 1)
        esegui_piano(plan)
        self.assertEqual(Cliente.objects.get().pec, 'alfa@pec.it')

    def test_existing_sector_is_reused_by_slug_and_name(self):
        sector = Settore.objects.create(nome='Tecnologie & servizi', slug='tecnologie-servizi')
        plan = self.prepare([self.row(settore='Tecnologie servizi')])
        self.assertEqual(plan['rows'][0]['_sector']['id'], sector.pk)
        sector_change = next(change for change in plan['rows'][0]['changes'] if change['field'] == 'settore')
        self.assertEqual(sector_change['after'], sector.nome)
        esegui_piano(plan)
        self.assertEqual(Settore.objects.count(), 1)
        self.assertEqual(Cliente.objects.get().settore_id, sector.pk)

    def test_new_sector_variants_share_one_creation_and_same_preview_name(self):
        plan = self.prepare([self.row(settore='Ricerca & sviluppo'),
                             self.row(name='Beta', vat='22345678901', settore='Ricerca sviluppo')])
        self.assertEqual(plan['rows'][0]['_sector'], plan['rows'][1]['_sector'])
        counts = esegui_piano(plan)
        self.assertEqual(counts['create'], 2)
        self.assertEqual(Settore.objects.count(), 1)
        self.assertEqual(Cliente.objects.values('settore_id').distinct().count(), 1)

    def test_sector_name_and_slug_collision_is_explicit_error(self):
        Settore.objects.create(nome='Servizi', slug='custom')
        Settore.objects.create(nome='Altro', slug='servizi')
        plan = self.prepare([self.row(settore='Servizi')])
        self.assertEqual(plan['counts']['error'], 1)
        self.assertIn('Settore ambiguo', plan['rows'][0]['error'])

    def test_fingerprint_is_deterministic_and_changes_when_customer_state_changes(self):
        customer = Cliente.objects.create(ragione_sociale='Alfa', partita_iva='IT12345678901')
        rows = [self.row(citta='Milano')]
        plan = self.prepare(rows)
        self.assertEqual(plan['fingerprint'], self.prepare(rows)['fingerprint'])
        # Anche un campo non importato conta: il piano autorizzato riguarda la
        # stessa versione dell'anagrafica, non soltanto i valori della diff.
        Cliente.objects.filter(pk=customer.pk).update(numero_dipendenti='50')
        self.assertNotEqual(plan['fingerprint'], self.prepare(rows)['fingerprint'])

    def test_fingerprint_changes_when_customer_created_deleted_or_duplicated(self):
        rows = [self.row()]
        empty = self.prepare(rows)['fingerprint']
        customer = Cliente.objects.create(ragione_sociale='Alfa', partita_iva='12345678901')
        existing = self.prepare(rows)['fingerprint']
        self.assertNotEqual(empty, existing)
        other = Cliente.objects.create(ragione_sociale='Altra', partita_iva='IT12345678901')
        self.assertNotEqual(existing, self.prepare(rows)['fingerprint'])
        other.delete()
        customer.delete()
        self.assertEqual(empty, self.prepare(rows)['fingerprint'])

    def test_fingerprint_tracks_relevant_sectors_but_not_unrelated_customers(self):
        rows = [self.row(settore='Tecnologia')]
        plan = self.prepare(rows)
        Cliente.objects.create(ragione_sociale='Estranea', partita_iva='IT99999999999')
        self.assertEqual(plan['fingerprint'], self.prepare(rows)['fingerprint'])
        sector = Settore.objects.create(nome='Tecnologia')
        created = self.prepare(rows)['fingerprint']
        self.assertNotEqual(plan['fingerprint'], created)
        Settore.objects.filter(pk=sector.pk).update(nome='Technology')
        self.assertNotEqual(created, self.prepare(rows)['fingerprint'])

    def test_confirmation_can_lock_and_matches_unlocked_preview(self):
        Cliente.objects.create(ragione_sociale='Alfa', partita_iva='IT12345678901')
        rows = [self.row(name='Nuovo nome')]
        preview = self.prepare(rows)
        with transaction.atomic():
            locked = self.prepare(rows, lock=True)
            self.assertEqual(locked['fingerprint'], preview['fingerprint'])
            self.assertEqual(esegui_piano(locked)['update'], 1)

    def test_unexpected_save_conflict_rolls_back_whole_batch_including_sectors_and_audit(self):
        plan = self.prepare([self.row(settore='Nuovo'), self.row(name='Beta', vat='22345678901', settore='Altro')])
        before_audit = AuditLog.objects.count()
        original_save = Cliente.save

        def save_or_fail(instance, *args, **kwargs):
            if instance.ragione_sociale == 'Beta':
                raise IntegrityError('Simulazione conflitto concorrente')
            return original_save(instance, *args, **kwargs)

        with patch.object(Cliente, 'save', save_or_fail):
            with self.assertRaises(IntegrityError):
                esegui_piano(plan)
        self.assertFalse(Cliente.objects.exists())
        self.assertFalse(Settore.objects.exists())
        self.assertEqual(AuditLog.objects.count(), before_audit)

    def test_foreign_vat_and_country_are_normalized_without_italian_checksum_requirement(self):
        plan = self.prepare([self.row(vat='DE 123456789', nazione='de')])
        self.assertEqual(plan['counts']['create'], 1)
        esegui_piano(plan)
        customer = Cliente.objects.get()
        self.assertEqual(customer.partita_iva, 'DE123456789')
        self.assertEqual(customer.nazione, 'DE')

    def test_missing_country_defaults_to_italy_without_inventing_foreign_vat(self):
        for vat in ('non-valida', 'DE123456789'):
            with self.subTest(vat=vat):
                plan = self.prepare([self.row(vat=vat)])
                self.assertEqual(plan['counts']['error'], 1)
                self.assertIn('Partita IVA:', plan['rows'][0]['error'])

    def test_invalid_italian_postcode_sdi_and_tax_code_are_excluded(self):
        for field, value in [('cap', 'ABCDE'), ('codice_sdi', 'TOO-LONG'), ('codice_fiscale', 'INVALID')]:
            with self.subTest(field=field):
                headers = ['ragione_sociale', 'partita_iva', field]
                plan = self.prepare([['Alfa', '12345678901', value]], headers=headers)
                self.assertEqual(plan['counts']['error'], 1)
                self.assertTrue(plan['rows'][0]['error'])

    def test_invalid_row_never_creates_its_sector(self):
        plan = self.prepare([self.row(settore='Da non creare', pec='invalida'),
                             self.row(name='Beta', vat='22345678901', settore='Valido')])
        counts = esegui_piano(plan)
        self.assertEqual(counts, {'create': 1, 'update': 0, 'unchanged': 0, 'error': 1})
        self.assertEqual(list(Settore.objects.values_list('nome', flat=True)), ['Valido'])

    def test_preview_queries_do_not_grow_per_customer_or_sector(self):
        sector = Settore.objects.create(nome='Settore condiviso')
        rows = []
        for index in range(15):
            vat = f'IT{index:011d}'
            Cliente.objects.create(ragione_sociale=f'Azienda {index}', partita_iva=vat, settore=sector)
            rows.append(self.row(name=f'Azienda {index}', vat=vat, settore=sector.nome))
        with self.assertNumQueries(2):
            plan = self.prepare(rows)
        self.assertEqual(plan['counts']['unchanged'], 15)
