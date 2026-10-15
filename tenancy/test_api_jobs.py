"""Regressioni per i confini di azienda nelle integrazioni e nei job."""
import gzip
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from accounts.models import Profile
from anagrafiche.models import PosizioneIva
from api.models import ApiToken, Webhook
from api.webhooks import dispatch
from clienti.models import Cliente, Tag
from documenti.models import TestataFattura

from .context import azienda_context, current_azienda_id
from .models import Azienda


class AziendeBase(TestCase):
    def setUp(self):
        self.a = Azienda.objects.get(pk=1)
        self.b = Azienda.objects.create(nome='Azienda B')
        with azienda_context(self.a.pk):
            self.user_a = get_user_model().objects.create_user('api-azienda-a', password='pw')
            self.token_a = ApiToken.objects.create(user=self.user_a, name='A', scrittura=True)
            self.iva_a = PosizioneIva.objects.create(descrizione='IVA A', aliquota=22)
            self.cliente_a = Cliente.objects.create(ragione_sociale='Cliente A', posizione_iva=self.iva_a)
        with azienda_context(self.b.pk):
            self.user_b = get_user_model().objects.create_user('api-azienda-b', password='pw')
            self.token_b = ApiToken.objects.create(user=self.user_b, name='B', scrittura=True)
            self.iva_b = PosizioneIva.objects.create(descrizione='IVA B', aliquota=22)
            self.cliente_b = Cliente.objects.create(ragione_sociale='Cliente B', posizione_iva=self.iva_b)

    def get(self, path, token):
        return self.client.get(path, HTTP_AUTHORIZATION=f'Bearer {token.token_in_chiaro}')

    def post(self, path, data, token):
        return self.client.post(path, json.dumps(data), content_type='application/json',
                                HTTP_AUTHORIZATION=f'Bearer {token.token_in_chiaro}')

    def rpc(self, name, arguments, token):
        return self.post('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                  'params': {'name': name, 'arguments': arguments}}, token)


class ApiAziendaTests(AziendeBase):
    def test_api_v1_v2_lists_and_direct_ids_are_isolated(self):
        for version in ('v1', 'v2'):
            for token, own, foreign in ((self.token_a, self.cliente_a, self.cliente_b),
                                        (self.token_b, self.cliente_b, self.cliente_a)):
                with self.subTest(version=version, azienda=token.azienda_id):
                    response = self.get(f'/api/{version}/clienti', token)
                    self.assertEqual(response.status_code, 200, response.content)
                    self.assertEqual([c['id'] for c in response.json()['results']], [own.pk])
                    self.assertEqual(self.get(f'/api/{version}/clienti/{foreign.pk}', token).status_code, 404)

    def test_token_overrides_browser_session_company_and_restores_context(self):
        self.client.force_login(self.user_a)
        with azienda_context(self.a.pk):
            response = self.get('/api/v2/clienti', self.token_b)
            self.assertEqual([c['id'] for c in response.json()['results']], [self.cliente_b.pk])
            self.assertEqual(current_azienda_id(), self.a.pk)

    def test_v2_write_rejects_foreign_customer_and_assigns_company(self):
        data = {'cliente_id': self.cliente_a.pk,
                'righe': [{'descrizione': 'Consulenza', 'importo_unitario': '100'}]}
        response = self.post('/api/v2/fatture', data, self.token_b)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(TestataFattura.all_objects.exists())
        data['cliente_id'] = self.cliente_b.pk
        response = self.post('/api/v2/fatture', data, self.token_b)
        self.assertEqual(response.status_code, 201, response.content)
        saved = TestataFattura.all_objects.get(pk=response.json()['id'])
        self.assertEqual(saved.azienda_id, self.b.pk)

    def test_mcp_lists_and_foreign_customer_write_are_isolated(self):
        response = self.rpc('cerca_clienti', {}, self.token_b)
        result = response.json()['result']
        self.assertFalse(result['isError'], result)
        self.assertIn('Cliente B', json.dumps(result))
        self.assertNotIn('Cliente A', json.dumps(result))
        response = self.rpc('crea_fattura', {'cliente': self.cliente_a.pk,
                            'righe': [{'descrizione': 'Consulenza', 'prezzo': 100}]}, self.token_b)
        self.assertTrue(response.json()['result']['isError'])
        self.assertFalse(TestataFattura.all_objects.exists())

    def _assert_token_rejected_everywhere(self, token):
        for path in ('/api/v1/health', '/api/v2/health'):
            self.assertEqual(self.get(path, token).status_code, 401)
        response = self.post('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, token)
        self.assertEqual(response.status_code, 401)

    def test_moved_user_cannot_reuse_token_of_previous_company(self):
        Profile.all_objects.filter(user=self.user_b).update(azienda=self.a)
        self._assert_token_rejected_everywhere(self.token_b)

    def test_disabled_company_cannot_authenticate_with_token(self):
        Azienda.objects.filter(pk=self.b.pk).update(attiva=False)
        self._assert_token_rejected_everywhere(self.token_b)

    def test_invited_user_with_valid_token_is_allowed(self):
        Profile.all_objects.filter(user=self.user_b).update(stato='invitato')
        for path in ('/api/v1/health', '/api/v2/health'):
            self.assertEqual(self.get(path, self.token_b).status_code, 200)
        response = self.post('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}, self.token_b)
        self.assertEqual(response.status_code, 200)

    def test_suspended_profile_cannot_authenticate_with_token(self):
        Profile.all_objects.filter(user=self.user_b).update(stato='sospeso')
        self._assert_token_rejected_everywhere(self.token_b)


@override_settings(WEBHOOK_IN_BACKGROUND=False)
class WebhookAziendaTests(AziendeBase):
    def setUp(self):
        super().setUp()
        with azienda_context(self.a.pk):
            self.hook_a = Webhook.objects.create(name='A', url='https://example.com/a')
        with azienda_context(self.b.pk):
            self.hook_b = Webhook.objects.create(name='B', url='https://example.com/b')

    def test_deferred_payload_and_destination_keep_original_company(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            with azienda_context(self.a.pk):
                dispatch('cliente.creato', lambda: {'ids': list(Cliente.objects.values_list('pk', flat=True))})
        delivered = []
        with patch('api.webhooks._deliver', side_effect=lambda hook, event, data:
                   delivered.append((hook.pk, current_azienda_id(), data))):
            with azienda_context(self.b.pk):
                for callback in callbacks:
                    callback()
                self.assertEqual(current_azienda_id(), self.b.pk)
        self.assertEqual(delivered, [(self.hook_a.pk, self.a.pk, {'ids': [self.cliente_a.pk]})])

    @override_settings(WEBHOOK_IN_BACKGROUND=True)
    def test_background_delivery_restores_company_in_worker(self):
        with patch('api.webhooks.threading.Thread') as thread:
            with self.captureOnCommitCallbacks(execute=True):
                with azienda_context(self.b.pk):
                    dispatch('cliente.creato', {'id': self.cliente_b.pk})
            kwargs = thread.call_args.kwargs
        delivered = []
        with patch('api.webhooks._deliver', side_effect=lambda hook, event, data:
                   delivered.append((hook.pk, current_azienda_id()))):
            with azienda_context(None):
                kwargs['target'](*kwargs['args'], chiudi_connessioni=False)
                self.assertIsNone(current_azienda_id())
        self.assertEqual(delivered, [(self.hook_b.pk, self.b.pk)])

    def test_unknown_company_does_not_dispatch(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            with azienda_context(None):
                dispatch('cliente.creato', {'id': self.cliente_a.pk})
        self.assertEqual(callbacks, [])


class JobAziendaTests(AziendeBase):
    def test_server_backup_includes_all_companies_and_their_relations(self):
        with azienda_context(self.b.pk):
            tag = Tag.objects.create(nome='Tag B')
            self.cliente_b.tags.add(tag)
        with tempfile.TemporaryDirectory() as directory:
            with self.settings(BASE_DIR=Path(directory)):
                with azienda_context(self.a.pk):
                    call_command('backup_db', stdout=io.StringIO())
            [path] = list((Path(directory) / 'backups').glob('*.json.gz'))
            with gzip.open(path, 'rt', encoding='utf-8') as data:
                records = json.load(data)
        customers = {row['pk']: row['fields'] for row in records if row['model'] == 'clienti.cliente'}
        self.assertEqual(set(customers), {self.cliente_a.pk, self.cliente_b.pk})
        self.assertEqual(customers[self.cliente_b.pk]['tags'], [tag.pk])

    def test_timer_visits_each_active_company_and_restores_context(self):
        Azienda.objects.create(nome='Disattivata', attiva=False)
        visited = []
        with patch('api.models.RichiestaIdempotente.pulisci',
                   side_effect=lambda: visited.append(current_azienda_id()) or 0):
            with azienda_context(None):
                call_command('pulisci_idempotenza', stdout=io.StringIO())
                self.assertIsNone(current_azienda_id())
        self.assertEqual(visited, [self.a.pk, self.b.pk])

    def test_explicit_company_and_invalid_company(self):
        visited = []
        with patch('api.models.RichiestaIdempotente.pulisci',
                   side_effect=lambda: visited.append(current_azienda_id()) or 0):
            call_command('pulisci_idempotenza', azienda=self.b.pk, stdout=io.StringIO())
            with self.assertRaises(CommandError):
                call_command('pulisci_idempotenza', azienda=99999, stdout=io.StringIO())
        self.assertEqual(visited, [self.b.pk])

    def test_failed_company_does_not_stop_other_companies(self):
        visited = []

        def clean():
            visited.append(current_azienda_id())
            if current_azienda_id() == self.a.pk:
                raise CommandError('Configurazione mancante')
            return 0

        with patch('api.models.RichiestaIdempotente.pulisci', side_effect=clean):
            with self.assertRaises(CommandError):
                call_command('pulisci_idempotenza', stdout=io.StringIO(), stderr=io.StringIO())
        self.assertEqual(visited, [self.a.pk, self.b.pk])
