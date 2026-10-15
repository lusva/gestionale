from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from attivita.models import Attivita
from clienti.models import Cliente, Contatto
from documenti.models import TestataOfferta

from .views import _search_documenti


class RicercaTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='ricerca')
        cls.azienda = Cliente.objects.create(ragione_sociale='Alfa Servizi Srl', citta='Milano')
        cls.contatto = Contatto.objects.create(
            cliente=cls.azienda, nome='Mario', cognome='Rossi',
            telefono='+39 333 1234567', email='mario@example.com',
        )
        cls.attivita = Attivita.objects.create(
            cliente=cls.azienda, titolo='Richiamare referente', data=timezone.now(),
        )
        TestataOfferta.objects.bulk_create([
            TestataOfferta(cliente=cls.azienda, numero=12, anno=2026, data_documento=date(2026, 1, 1)),
            TestataOfferta(cliente=cls.azienda, numero=12, anno=2025, data_documento=date(2025, 1, 1)),
            TestataOfferta(cliente=cls.azienda, numero=112, anno=2026, data_documento=date(2026, 1, 1)),
        ])

    def setUp(self):
        self.client.force_login(self.user)

    def test_nome_completo_in_campi_distinti(self):
        response = self.client.get(reverse('search:cerca'), {'q': ' Mario   Rossi '})
        self.assertEqual(response.status_code, 200)
        groups = {g['kind']: g for g in response.context['groups']}
        self.assertEqual([c.pk for c in groups['contatti']['items']], [self.contatto.pk])
        self.assertEqual([c.pk for c in groups['clienti']['items']], [self.azienda.pk])
        self.assertEqual(response.context['q'], 'Mario Rossi')

    def test_parole_in_ordine_diverso_e_nome_azienda(self):
        response = self.client.get(reverse('search:rapida'), {'q': 'Rossi Alfa'})
        groups = {g['nome']: g['voci'] for g in response.json()['gruppi']}
        self.assertEqual(groups['Contatti'][0]['titolo'], 'Mario Rossi')
        self.assertEqual(groups['Clienti'][0]['titolo'], 'Alfa Servizi Srl')

    def test_ricerca_telefono_e_attivita_in_palette(self):
        response = self.client.get(reverse('search:rapida'), {'q': '1234567'})
        self.assertIn('Contatti', [g['nome'] for g in response.json()['gruppi']])
        response = self.client.get(reverse('search:rapida'), {'q': 'Alfa richiamare'})
        groups = {g['nome']: g['voci'] for g in response.json()['gruppi']}
        self.assertEqual(groups['Attività'][0]['url'], self.attivita.get_absolute_url())

    def test_documenti_per_numero_e_anno(self):
        for query in ('12/2026', '12-2026'):
            with self.subTest(query=query):
                response = self.client.get(reverse('search:cerca'), {'q': query, 'group': 'documenti'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['total_focus'], 1)
                item = response.context['items'][0]
                self.assertEqual((item['numero'], item['anno']), (12, 2026))

    def test_documenti_disabilitati_non_rompono_il_focus(self):
        with patch('search.views._DOCUMENTI_AVAILABLE', False):
            response = self.client.get(reverse('search:cerca'), {'q': 'Alfa', 'group': 'documenti'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['mode'], 'overview')

    def test_ricerca_completa_rispetta_permessi_clienti_e_documenti(self):
        with patch('search.views.has_perm', return_value=False):
            response = self.client.get(reverse('search:cerca'), {'q': 'Alfa', 'group': 'documenti'})
        self.assertEqual(response.status_code, 200)
        groups = {g['kind']: g for g in response.context['groups']}
        for kind in ('documenti', 'clienti', 'contatti'):
            self.assertEqual(groups[kind]['count'], 0)
            self.assertEqual(groups[kind]['items'], [])

    def test_paginazione_documenti_senza_duplicati(self):
        TestataOfferta.objects.bulk_create([
            TestataOfferta(cliente=self.azienda, numero=i, anno=2027, data_documento=date(2027, 1, 1))
            for i in range(1, 24)
        ])
        results = _search_documenti('Alfa')
        first, second = results[:20], results[20:40]
        urls = [item['url'] for item in first + second]
        self.assertEqual(len(urls), results.count())
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(first[0]['numero'], 23)

    def test_ricerca_richiede_login(self):
        self.client.logout()
        for name in ('search:cerca', 'search:rapida'):
            self.assertEqual(self.client.get(reverse(name), {'q': 'Alfa'}).status_code, 302)
