from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Ruolo
from audit.models import AuditLog, Azione
from clienti.models import Cliente
from opportunita.models import Opportunita

from .forms import AttivitaForm
from .models import Attivita


class AgendaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('maria', password='test-password')
        cls.colleague = get_user_model().objects.create_user('luca')
        cls.viewer = get_user_model().objects.create_user('lettore')
        cls.viewer.profile.ruolo = Ruolo.VISUALIZZATORE
        cls.viewer.profile.save()
        cls.cliente = Cliente.objects.create(ragione_sociale='Acme Srl')
        cls.other_cliente = Cliente.objects.create(ragione_sociale='Beta Srl')
        cls.opportunita = Opportunita.objects.create(
            cliente=cls.cliente, titolo='Rinnovo licenze annuali', owner=cls.user,
        )
        cls.now = datetime(2026, 10, 15, 12, 0, tzinfo=ZoneInfo('Europe/Rome'))
        cls.overdue = Attivita.objects.create(
            titolo='Richiamare amministrazione', tipo='call', data=cls.now - timedelta(days=2),
            owner=cls.user, cliente=cls.cliente, opportunita=cls.opportunita,
        )
        cls.today = Attivita.objects.create(
            titolo='Inviare proposta', tipo='email', data=cls.now + timedelta(hours=2),
            owner=cls.user, cliente=cls.cliente, descrizione='Preventivo personalizzato',
        )
        cls.upcoming = Attivita.objects.create(
            titolo='Presentazione progetto', tipo='meeting', data=cls.now + timedelta(days=3),
            owner=cls.user, cliente=cls.cliente,
        )
        cls.completed = Attivita.objects.create(
            titolo='Primo contatto', tipo='call', data=cls.now - timedelta(days=5),
            owner=cls.user, cliente=cls.cliente, completata=True,
        )
        cls.other_activity = Attivita.objects.create(
            titolo='Verifica interna', tipo='task', data=cls.now - timedelta(hours=1),
            owner=cls.colleague,
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.clock = patch('attivita.views.timezone.now', return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def agenda(self, **params):
        return self.client.get(reverse('attivita:list'), params)

    def ids(self, response):
        return [item.pk for item in response.context['attivita_list']]

    def form_data(self, **changes):
        data = {
            'titolo': 'Follow-up offerta', 'tipo': 'call',
            'data': '2026-10-16T10:00', 'durata_minuti': '30',
            'owner': self.user.pk, 'cliente': '', 'opportunita': '',
            'descrizione': 'Confermare il prossimo passo',
        }
        data.update(changes)
        return data

    def test_default_agenda_prioritizes_open_and_overdue_activities(self):
        response = self.agenda()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.ids(response), [
            self.overdue.pk, self.other_activity.pk, self.today.pk, self.upcoming.pk,
        ])
        self.assertContains(response, self.overdue.get_absolute_url())
        self.assertContains(response, 'Scaduta')
        self.assertNotContains(response, f'id="attivita-{self.completed.pk}"')

    def test_search_type_owner_status_and_period_combine(self):
        response = self.agenda(q='Acme', tipo='call', owner='me', stato='da_fare', periodo='scadute')
        self.assertEqual(self.ids(response), [self.overdue.pk])
        self.assertContains(response, 'owner=me')
        self.assertContains(response, 'q=Acme')
        self.assertContains(response, 'tipo=call')

    def test_search_finds_opportunity_and_description(self):
        self.assertEqual(self.ids(self.agenda(q='licenze annuali')), [self.overdue.pk])
        self.assertEqual(self.ids(self.agenda(q='personalizzato')), [self.today.pk])

    def test_today_uses_local_midnight_boundaries(self):
        midnight = self.now.replace(hour=0, minute=0)
        first = Attivita.objects.create(titolo='Inizio giornata', data=midnight, owner=self.user)
        last = Attivita.objects.create(
            titolo='Fine giornata', data=midnight + timedelta(days=1, seconds=-1), owner=self.user,
        )
        before = Attivita.objects.create(titolo='Ieri', data=midnight - timedelta(seconds=1))
        after = Attivita.objects.create(titolo='Domani', data=midnight + timedelta(days=1))
        result_ids = self.ids(self.agenda(periodo='oggi'))
        self.assertIn(first.pk, result_ids)
        self.assertIn(last.pk, result_ids)
        self.assertNotIn(before.pk, result_ids)
        self.assertNotIn(after.pk, result_ids)

    def test_week_contains_seven_local_calendar_days(self):
        start = self.now.replace(hour=0, minute=0)
        last = Attivita.objects.create(titolo='Ultimo giorno', data=start + timedelta(days=7, seconds=-1))
        after = Attivita.objects.create(titolo='Fuori settimana', data=start + timedelta(days=7))
        result_ids = self.ids(self.agenda(periodo='settimana'))
        self.assertIn(last.pk, result_ids)
        self.assertNotIn(after.pk, result_ids)
        self.assertNotIn(self.overdue.pk, result_ids)

    def test_summary_respects_owner_search_type_without_hiding_other_periods(self):
        response = self.agenda(q='Acme', owner='me', tipo='call', stato='completate')
        self.assertEqual(self.ids(response), [self.completed.pk])
        self.assertEqual(response.context['stats'], {'aperte': 1, 'scadute': 1, 'oggi': 0, 'settimana': 0})

    def test_htmx_toggle_removes_filtered_row_and_refreshes_counts_and_actions(self):
        response = self.client.post(
            reverse('attivita:toggle', args=[self.overdue.pk]) + '?stato=da_fare&owner=me',
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['HX-Trigger'], 'crm:changed')
        self.overdue.refresh_from_db()
        self.assertTrue(self.overdue.completata)
        self.assertNotContains(response, f'id="attivita-{self.overdue.pk}"')
        self.assertContains(response, 'id="agenda-results"')
        self.assertContains(response, reverse('attivita:update', args=[self.today.pk]))
        self.assertContains(response, reverse('attivita:delete', args=[self.today.pk]))
        self.assertContains(response, 'Attività completata.')
        self.assertEqual(response.context['stats']['aperte'], 2)
        self.assertEqual(response.context['stats']['scadute'], 0)

    def test_completing_last_row_of_last_page_returns_existing_page(self):
        Attivita.objects.all().delete()
        activities = [Attivita.objects.create(
            titolo=f'Attività {index}', data=self.now + timedelta(minutes=index), owner=self.user,
        ) for index in range(21)]
        response = self.client.post(
            reverse('attivita:toggle', args=[activities[-1].pk]) + '?page=2&stato=da_fare',
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].number, 1)
        self.assertEqual(response.context['paginator'].count, 20)
        self.assertEqual(len(response.context['attivita_list']), 20)

    def test_toggle_normal_post_redirects_back_and_announces_success(self):
        target = reverse('attivita:list') + '?owner=me&stato=da_fare'
        response = self.client.post(
            reverse('attivita:toggle', args=[self.overdue.pk]), {'next': target},
        )
        self.assertRedirects(response, target, fetch_redirect_response=False)
        response = self.client.get(target)
        self.assertContains(response, 'Attività completata.')

    def test_toggle_rejects_external_redirect_and_get_never_mutates(self):
        url = reverse('attivita:toggle', args=[self.overdue.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.overdue.refresh_from_db()
        self.assertFalse(self.overdue.completata)
        response = self.client.post(url, {'next': 'https://example.org/collect'})
        self.assertRedirects(response, reverse('attivita:list'))

    def test_toggle_records_one_audit_update_with_authenticated_actor(self):
        updates = AuditLog.objects.filter(
            target_type='Attivita', target_id=str(self.overdue.pk), azione=Azione.UPDATE,
        )
        before = updates.count()
        response = self.client.post(
            reverse('attivita:toggle', args=[self.overdue.pk]), HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(updates.count(), before + 1)
        event = updates.latest('pk')
        self.assertEqual(event.actor_id, self.user.pk)
        self.assertEqual(event.target_label, self.overdue.titolo)
        self.assertEqual(event.ip, '127.0.0.1')

    def test_viewer_sees_details_but_no_edit_controls_and_cannot_toggle(self):
        self.client.force_login(self.viewer)
        response = self.agenda()
        self.assertContains(response, self.overdue.get_absolute_url())
        self.assertNotContains(response, reverse('attivita:create'))
        self.assertNotContains(response, reverse('attivita:update', args=[self.overdue.pk]))
        detail = self.client.get(self.overdue.get_absolute_url())
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, 'Segna come completata')
        response = self.client.post(reverse('attivita:toggle', args=[self.overdue.pk]))
        self.assertEqual(response.status_code, 403)
        self.overdue.refresh_from_db()
        self.assertFalse(self.overdue.completata)

    def test_create_prefills_owner_client_and_opportunity(self):
        url = reverse('attivita:create')
        self.assertEqual(self.client.get(url).context['form'].initial['owner'], self.user.pk)
        response = self.client.get(url, {
            'opportunita': self.opportunita.pk, 'cliente': self.other_cliente.pk,
            'owner': self.colleague.pk,
        })
        initial = response.context['form'].initial
        self.assertEqual(initial['owner'], self.colleague.pk)
        self.assertEqual(initial['cliente'], self.cliente.pk)
        self.assertEqual(initial['opportunita'], self.opportunita.pk)

    def test_form_infers_client_from_opportunity(self):
        response = self.client.post(reverse('attivita:create'), self.form_data(opportunita=self.opportunita.pk))
        self.assertRedirects(response, reverse('attivita:list'))
        activity = Attivita.objects.get(titolo='Follow-up offerta')
        self.assertEqual(activity.cliente_id, self.cliente.pk)
        self.assertEqual(activity.opportunita_id, self.opportunita.pk)
        self.assertEqual(timezone.localtime(activity.data).hour, 10)

    def test_form_rejects_opportunity_from_another_client_and_renders_error(self):
        count = Attivita.objects.count()
        response = self.client.post(reverse('attivita:create'), self.form_data(
            cliente=self.other_cliente.pk, opportunita=self.opportunita.pk,
        ))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'appartiene a un altro cliente')
        self.assertIn('opportunita', response.context['form'].errors)
        self.assertEqual(Attivita.objects.count(), count)

    def test_update_rejects_client_mismatch_too(self):
        form = AttivitaForm(
            self.form_data(cliente=self.other_cliente.pk, opportunita=self.opportunita.pk),
            instance=self.overdue,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('opportunita', form.errors)

    def test_pagination_preserves_filters(self):
        for index in range(21):
            Attivita.objects.create(
                titolo=f'Verifica {index}', data=self.now, owner=self.user, tipo='call',
            )
        response = self.agenda(q='Verifica', tipo='call', owner='me', stato='da_fare', periodo='oggi')
        self.assertTrue(response.context['is_paginated'])
        self.assertContains(response, 'q=Verifica&amp;tipo=call&amp;owner=me&amp;stato=da_fare&amp;periodo=oggi&amp;page=2')
