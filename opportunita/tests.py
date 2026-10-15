from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Ruolo
from attivita.models import Attivita
from clienti.models import Cliente

from .forms import OpportunitaForm
from .models import Opportunita, Stadio
from .views import _pipeline_context


class OpportunitaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager = get_user_model().objects.create_user('manager', password='test-password')
        cls.viewer = get_user_model().objects.create_user('viewer', password='test-password')
        cls.viewer.profile.ruolo = Ruolo.VISUALIZZATORE
        cls.viewer.profile.save()
        cls.customer = Cliente.objects.create(ragione_sociale='Acme Innovazione')

    def setUp(self):
        self.client.force_login(self.manager)

    def opportunity(self, **kwargs):
        data = {'cliente': self.customer, 'titolo': 'Audit CRM', 'owner': self.manager,
                'valore': Decimal('1000'), 'probabilita': 50}
        data.update(kwargs)
        return Opportunita.objects.create(**data)

    def form_data(self, opp, **kwargs):
        data = {'cliente': opp.cliente_id, 'titolo': opp.titolo, 'descrizione': '',
                'valore': opp.valore, 'probabilita': opp.probabilita, 'stadio': opp.stadio,
                'owner': self.manager.pk, 'chiusura_prevista': ''}
        data.update(kwargs)
        return data

    def test_form_transition_and_reopening_preserve_stage_invariants(self):
        opp = self.opportunity(ingresso_stadio=timezone.now() - timedelta(days=20))
        form = OpportunitaForm(self.form_data(opp, stadio=Stadio.CHIUSA_WIN, probabilita=70), instance=opp)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        opp.refresh_from_db()
        self.assertEqual(opp.probabilita, 100)
        self.assertEqual(opp.data_chiusura, timezone.localdate())
        self.assertEqual(opp.giorni_in_stadio, 0)
        form = OpportunitaForm(self.form_data(opp, stadio=Stadio.PROPOSTA), instance=opp)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        opp.refresh_from_db()
        self.assertIsNone(opp.data_chiusura)
        self.assertEqual(opp.probabilita, 25)

    def test_edit_without_transition_preserves_stage_entry_and_close_date(self):
        opp = self.opportunity(stadio=Stadio.CHIUSA_LOST)
        old_date = timezone.localdate() - timedelta(days=5)
        old_entry = timezone.now() - timedelta(days=5)
        Opportunita.objects.filter(pk=opp.pk).update(data_chiusura=old_date, ingresso_stadio=old_entry)
        opp.refresh_from_db()
        opp.titolo = 'Titolo aggiornato'
        opp.save()
        opp.refresh_from_db()
        self.assertEqual(opp.data_chiusura, old_date)
        self.assertEqual(opp.ingresso_stadio, old_entry)
        self.assertEqual(opp.probabilita, 0)

    def test_direct_stage_change_updates_all_derived_fields_and_rejects_invalid_stage(self):
        opp = self.opportunity(ingresso_stadio=timezone.now() - timedelta(days=20))
        opp.cambia_stadio(Stadio.CHIUSA_LOST)
        opp.refresh_from_db()
        self.assertEqual(opp.probabilita, 0)
        self.assertEqual(opp.data_chiusura, timezone.localdate())
        self.assertEqual(opp.giorni_in_stadio, 0)
        opp.cambia_stadio(Stadio.NUOVA)
        opp.refresh_from_db()
        self.assertEqual(opp.probabilita, 25)
        self.assertIsNone(opp.data_chiusura)
        with self.assertRaises(ValidationError):
            opp.cambia_stadio('unknown')

    def test_partial_probability_update_uses_persisted_stage(self):
        opp = self.opportunity(stadio=Stadio.CHIUSA_WIN)
        closed = opp.data_chiusura
        opp.stadio = Stadio.NUOVA
        opp.probabilita = 20
        opp.save(update_fields=['probabilita'])
        opp.refresh_from_db()
        self.assertEqual(opp.stadio, Stadio.CHIUSA_WIN)
        self.assertEqual(opp.probabilita, 100)
        self.assertEqual(opp.data_chiusura, closed)

    def test_form_rejects_probability_outside_range_and_negative_value(self):
        opp = self.opportunity()
        for probability in (-1, 101):
            with self.subTest(probability=probability):
                form = OpportunitaForm(self.form_data(opp, probabilita=probability), instance=opp)
                self.assertFalse(form.is_valid())
                self.assertIn('probabilita', form.errors)
        form = OpportunitaForm(self.form_data(opp, valore='-0.01'), instance=opp)
        self.assertFalse(form.is_valid())
        self.assertIn('valore', form.errors)

    def test_pipeline_kpis_exclude_closed_but_both_outcomes_are_visible(self):
        self.opportunity(valore=Decimal('1000'), probabilita=50)
        self.opportunity(valore=Decimal('2000'), probabilita=25, stadio=Stadio.PROPOSTA)
        won = self.opportunity(stadio=Stadio.CHIUSA_WIN, valore=Decimal('9000'))
        lost = self.opportunity(stadio=Stadio.CHIUSA_LOST, valore=Decimal('8000'))
        response = self.client.get(reverse('opportunita:list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['pipeline_count'], 2)
        self.assertEqual(response.context['pipeline_total'], Decimal('3000'))
        self.assertEqual(response.context['pipeline_weighted'], Decimal('1000'))
        self.assertContains(response, f'data-opp-id="{won.pk}"')
        self.assertContains(response, f'data-opp-id="{lost.pk}"')

    def test_combined_filters_and_stage_links(self):
        mine = self.opportunity(titolo='Rinnovo contratto', stadio=Stadio.PROPOSTA)
        self.opportunity(titolo='Rinnovo altro', owner=self.viewer, stadio=Stadio.PROPOSTA)
        self.opportunity(titolo='Rinnovo nuovo', stadio=Stadio.NUOVA)
        response = self.client.get(reverse('opportunita:list'), {'q': 'rinnovo', 'owner': 'me', 'scope': 'open', 'stadio': 'proposta'})
        self.assertEqual(response.context['pipeline_visible_count'], 1)
        self.assertContains(response, f'data-opp-id="{mine.pk}"')
        self.assertContains(response, 'value="proposta" selected')
        response = self.client.get(reverse('opportunita:list'), {'owner': 'not-an-id', 'stadio': 'invalid'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['pipeline_visible_count'], 3)

    def test_attention_filters_exclude_closed_and_use_pending_activities(self):
        overdue = self.opportunity(chiusura_prevista=timezone.localdate() - timedelta(days=1))
        stale = self.opportunity(ingresso_stadio=timezone.now() - timedelta(days=15))
        self.opportunity(stadio=Stadio.CHIUSA_LOST, chiusura_prevista=timezone.localdate() - timedelta(days=2))
        Attivita.objects.create(opportunita=overdue, titolo='Richiamare', data=timezone.now(), completata=False)
        Attivita.objects.create(opportunita=stale, titolo='Già fatto', data=timezone.now(), completata=True)
        for attention, expected in [('overdue', overdue.pk), ('stale', stale.pk), ('no_followup', stale.pk)]:
            with self.subTest(attention=attention):
                ctx = _pipeline_context({'attenzione': attention}, self.manager)
                items = [o.pk for col in ctx['pipeline_cols'] for o in col['items']]
                self.assertEqual(items, [expected])

    def test_pipeline_is_one_query_including_next_activity_and_owner(self):
        for idx in range(6):
            opp = self.opportunity(titolo=f'Trattativa {idx}')
            Attivita.objects.create(opportunita=opp, titolo='Completata', data=timezone.now() - timedelta(days=2), completata=True)
            next_activity = Attivita.objects.create(opportunita=opp, titolo='Richiamo urgente', data=timezone.now() - timedelta(days=1))
            Attivita.objects.create(opportunita=opp, titolo='Incontro futuro', data=timezone.now() + timedelta(days=1))
        with self.assertNumQueries(1):
            ctx = _pipeline_context({}, self.manager)
            items = [o for col in ctx['pipeline_cols'] for o in col['items']]
            self.assertEqual(len(items), 6)
            for item in items:
                self.assertEqual(item.next_attivita_titolo, 'Richiamo urgente')
                self.assertEqual(item.cliente.ragione_sociale, 'Acme Innovazione')
                self.assertTrue(item.owner.profile.iniziali)
            self.assertIn(next_activity.pk, [o.next_attivita_id for o in items])

    def test_create_prefills_requested_stage_client_and_owner(self):
        response = self.client.get(reverse('opportunita:create'), {'stadio': Stadio.CHIUSA_WIN, 'cliente': self.customer.pk})
        form = response.context['form']
        self.assertEqual(form.initial['stadio'], Stadio.CHIUSA_WIN)
        self.assertEqual(form.initial['probabilita'], 100)
        self.assertEqual(str(form.initial['cliente']), str(self.customer.pk))
        self.assertEqual(form.initial['owner'], self.manager.pk)

    def test_viewer_cannot_edit_and_controls_are_hidden(self):
        opp = self.opportunity()
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('opportunita:list'))
        self.assertNotContains(response, 'draggable="true"')
        self.assertNotContains(response, 'Pianifica follow-up')
        self.assertNotContains(response, 'Nuova opportunità')
        response = self.client.get(opp.get_absolute_url())
        self.assertNotContains(response, reverse('opportunita:update', args=[opp.pk]))
        self.assertNotContains(response, reverse('opportunita:delete', args=[opp.pk]))
        response = self.client.post(reverse('opportunita:sposta', args=[opp.pk]), {'stadio': Stadio.CHIUSA_WIN}, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 403)
        opp.refresh_from_db()
        self.assertEqual(opp.stadio, Stadio.NUOVA)

    def test_move_refreshes_kpis_and_keeps_active_filters(self):
        opp = self.opportunity(titolo='Visibile')
        other = self.opportunity(titolo='Nascosta')
        response = self.client.post(reverse('opportunita:sposta', args=[opp.pk]) + '?q=Visibile', {'stadio': Stadio.CHIUSA_WIN, 'posizione': '0'}, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="pipeline-results"', count=1)
        self.assertContains(response, 'id="pipeline-board"', count=1)
        self.assertContains(response, f'data-opp-id="{opp.pk}"')
        self.assertNotContains(response, f'data-opp-id="{other.pk}"')
        self.assertEqual(response.context['pipeline_total'], Decimal('0'))
        self.assertEqual(response.context['pipeline_count'], 0)

    def test_move_with_filters_orders_against_visible_cards(self):
        hidden = self.opportunity(titolo='Hidden', stadio=Stadio.PROPOSTA, ordine=0)
        first = self.opportunity(titolo='Visible first', stadio=Stadio.PROPOSTA, ordine=1)
        second = self.opportunity(titolo='Visible second', stadio=Stadio.PROPOSTA, ordine=2)
        moving = self.opportunity(titolo='Visible moving', stadio=Stadio.NUOVA)
        response = self.client.post(reverse('opportunita:sposta', args=[moving.pk]) + '?q=Visible', {'stadio': Stadio.PROPOSTA, 'posizione': '1'}, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(Opportunita.objects.filter(stadio=Stadio.PROPOSTA).order_by('ordine').values_list('pk', flat=True)), [hidden.pk, first.pk, moving.pk, second.pk])

    def test_stage_form_works_without_javascript(self):
        opp = self.opportunity()
        response = self.client.post(reverse('opportunita:sposta', args=[opp.pk]), {'stadio': Stadio.QUALIFICATA})
        self.assertRedirects(response, opp.get_absolute_url())
        opp.refresh_from_db()
        self.assertEqual(opp.stadio, Stadio.QUALIFICATA)
        response = self.client.post(reverse('opportunita:sposta', args=[opp.pk]), {'stadio': 'invalid'}, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 400)

    def test_unchanged_stage_form_does_not_reorder_opportunities(self):
        first = self.opportunity(ordine=0)
        second = self.opportunity(ordine=1)
        response = self.client.post(reverse('opportunita:sposta', args=[first.pk]), {'stadio': first.stadio})
        self.assertRedirects(response, first.get_absolute_url())
        self.assertEqual(list(Opportunita.objects.order_by('ordine').values_list('pk', flat=True)), [first.pk, second.pk])
