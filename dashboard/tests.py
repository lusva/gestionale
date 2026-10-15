from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Ruolo
from attivita.models import Attivita
from clienti.models import Cliente
from documenti.models import TestataFattura
from opportunita.models import Opportunita, Stadio

from .da_fare import conteggio, voci_da_fare
from .views import _fatturato_per_mese


class DashboardCRMTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('commerciale', password='test-only')
        cls.other = get_user_model().objects.create_user('collega')
        cls.cliente = Cliente.objects.create(ragione_sociale='Cliente dashboard')

    def setUp(self):
        self.client.force_login(self.user)

    def test_fatturato_non_somma_opportunita_vinte(self):
        Opportunita.objects.create(cliente=self.cliente, titolo='Contratto vinto',
                                   stadio=Stadio.CHIUSA_WIN, valore=Decimal('1200'))
        oggi = timezone.localdate()
        fattura = TestataFattura.objects.create(cliente=self.cliente, data_documento=oggi)
        # Il totale è derivato dalle righe; qui isoliamo l'aggregazione.
        TestataFattura.objects.filter(pk=fattura.pk).update(imponibile=Decimal('1200'))
        self.assertEqual(_fatturato_per_mese(oggi, oggi), {(oggi.year, oggi.month): 1200.0})

    def test_dashboard_usa_mese_locale_e_solo_pipeline_aperta(self):
        istante = datetime(2026, 9, 30, 22, 30, tzinfo=dt_timezone.utc)
        with patch('django.utils.timezone.now', return_value=istante):
            Opportunita.objects.create(cliente=self.cliente, titolo='Vinta a ottobre',
                                       stadio=Stadio.CHIUSA_WIN, valore=Decimal('800'))
            Opportunita.objects.create(cliente=self.cliente, titolo='Persa',
                                       stadio=Stadio.CHIUSA_LOST, valore=Decimal('500'))
            Opportunita.objects.create(cliente=self.cliente, titolo='Aperta', valore=Decimal('300'))
            response = self.client.get(reverse('dashboard:home'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['today'].date(), date(2026, 10, 1))
        self.assertEqual(response.context['kpi_fatturato_mtd'], Decimal('800'))
        self.assertEqual(response.context['pipeline_total'], Decimal('300'))
        self.assertEqual(response.context['pipeline_count'], 1)
        self.assertIsNone(response.context['kpi_fatturato_delta_pct'])
        self.assertContains(response, 'Vendite vinte nel mese')
        self.assertContains(response, 'Nessuna vendita nel mese precedente')
        self.assertNotContains(response, 'vs. trim. scorso')

    def test_agenda_solo_proprie_aperte_in_ordine_di_urgenza(self):
        now = timezone.now()
        futura = Attivita.objects.create(titolo='Da preparare', owner=self.user, data=now + timedelta(days=2))
        scaduta = Attivita.objects.create(titolo='Richiamare', owner=self.user, data=now - timedelta(days=1))
        Attivita.objects.create(titolo='Già fatta', owner=self.user, data=now, completata=True)
        Attivita.objects.create(titolo='Di un collega', owner=self.other, data=now - timedelta(days=3))
        response = self.client.get(reverse('dashboard:home'))
        self.assertEqual(list(response.context['prossime_attivita']), [scaduta, futura])
        self.assertEqual(response.context['agenda_counts']['scadute'], 1)

    def test_promemoria_linkano_filtri_e_contano_ritardi(self):
        now = timezone.now()
        Attivita.objects.create(titolo='Richiamare', owner=self.user, data=now - timedelta(days=1))
        Opportunita.objects.create(cliente=self.cliente, titolo='Rinnovo', owner=self.user,
                                   chiusura_prevista=timezone.localdate() - timedelta(days=1))
        Opportunita.objects.create(cliente=self.cliente, titolo='Di un collega', owner=self.other)
        voci = voci_da_fare(self.user)
        attivita = next(v for v in voci if 'attività in ritardo' in v.titolo)
        followup = next(v for v in voci if 'senza attività pianificate' in v.titolo)
        self.assertIn('owner=me&stato=da_fare&periodo=scadute', attivita.url)
        self.assertIn('attenzione=no_followup', followup.url)
        self.assertEqual(followup.conteggio, 1)
        self.assertEqual(conteggio(voci), 2)

    def test_visualizzatore_non_vede_azioni_di_modifica_cliente(self):
        self.user.profile.ruolo = Ruolo.VISUALIZZATORE
        self.user.profile.save()
        for tab in ('panoramica', 'contatti', 'attivita'):
            response = self.client.get(self.cliente.get_absolute_url(), {'tab': tab})
            self.assertEqual(response.status_code, 200)
            for action in ('clienti:update', 'clienti:delete'):
                self.assertNotContains(response, f'href="{reverse(action, args=[self.cliente.pk])}"')
            self.assertNotContains(response, f'href="{reverse("attivita:create")}?cliente=')
