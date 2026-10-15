import io
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import Profile, Ruolo, StatoUtente
from anagrafiche.models import AnagraficaAzienda
from api.models import ApiToken
from clienti.models import Cliente
from core_settings.models import Organizzazione
from tenancy.context import azienda_context, current_azienda_id
from tenancy.models import Azienda


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=['testserver'],
)
class CompanyOnboardingTests(TestCase):
    password = 'Password-test-onboarding-2026!'

    @classmethod
    def setUpTestData(cls):
        cls.pending = Azienda.objects.create(nome='Azienda da configurare', configurata=False)
        with azienda_context(cls.pending.pk):
            cls.admin = get_user_model().objects.create_user(
                username='nuovo-amministratore', password=cls.password,
                email='amministratore@nuova.example',
            )
            cls.admin.profile.ruolo = Ruolo.AMMINISTRATORE
            cls.admin.profile.save(update_fields=['ruolo'])
            cls.anagrafica = AnagraficaAzienda.objects.create(ragione_sociale='')
            Organizzazione.objects.create(nome=cls.pending.nome)
            cls.token = ApiToken.objects.create(user=cls.admin, name='Token di test')
            cls.raw_token = cls.token.token_in_chiaro
        cls.other = Azienda.objects.create(nome='Azienda esistente riservata')
        with azienda_context(cls.other.pk):
            cls.other_admin = get_user_model().objects.create_user(
                username='amministratore-esistente', password=cls.password,
            )
            cls.other_admin.profile.ruolo = Ruolo.AMMINISTRATORE
            cls.other_admin.profile.save(update_fields=['ruolo'])
            cls.other_anagrafica = AnagraficaAzienda.objects.create(
                ragione_sociale=cls.other.nome, email='segreto@esistente.example',
            )
            Organizzazione.objects.create(nome=cls.other.nome)

    def setUp(self):
        self.setup_url = reverse('accounts:configura_azienda')
        self.home_url = reverse('dashboard:home')
        with azienda_context(self.pending.pk):
            self.client.force_login(self.admin)

    def payload(self, **changes):
        data = {
            'ragione_sociale': 'Nuova Società SRL',
            'partita_iva': '12345678903',
            'codice_fiscale': '12345678903',
            'indirizzo_legale': 'Via Roma 42',
            'comune_legale': 'Milano',
            'cap_legale': '20100',
            'prov_legale': 'MI',
            'email': 'ufficio@nuova.example',
            'telefono': '+39 021234567',
        }
        data.update(changes)
        return data

    def assert_pending_unchanged(self):
        self.pending.refresh_from_db()
        self.assertFalse(self.pending.configurata)
        self.assertEqual(self.pending.nome, 'Azienda da configurare')
        self.assertEqual(
            AnagraficaAzienda.all_objects.get(pk=self.anagrafica.pk).ragione_sociale, '',
        )

    def test_command_without_company_name_creates_isolated_pending_administrator(self):
        output = io.StringIO()
        original_scope = current_azienda_id()
        with patch.dict('os.environ', {'TEST_ONBOARDING_PASSWORD': self.password}):
            call_command(
                'crea_azienda', username='da-configurare', email='nuovo@example.test',
                password_env='TEST_ONBOARDING_PASSWORD', stdout=output,
            )
        user = get_user_model().objects.get(username='da-configurare')
        profile = Profile.all_objects.select_related('azienda').get(user=user)
        self.assertNotIn(profile.azienda_id, (self.pending.pk, self.other.pk))
        self.assertFalse(profile.azienda.configurata)
        self.assertTrue(profile.azienda.attiva)
        self.assertEqual(profile.ruolo, Ruolo.AMMINISTRATORE)
        self.assertEqual(profile.stato, StatoUtente.ATTIVO)
        self.assertTrue(user.check_password(self.password))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        with azienda_context(profile.azienda_id):
            self.assertEqual(AnagraficaAzienda.objects.get().ragione_sociale, '')
            self.assertEqual(Organizzazione.objects.get().nome, profile.azienda.nome)
            self.assertFalse(Cliente.objects.exists())
        self.assertNotIn(self.password, output.getvalue())
        self.assertEqual(current_azienda_id(), original_scope)

    def test_command_with_name_preserves_existing_ready_company_behavior(self):
        with patch.dict('os.environ', {'TEST_ONBOARDING_PASSWORD': self.password}):
            call_command(
                'crea_azienda', 'Azienda già nominata', username='gia-configurato',
                password_env='TEST_ONBOARDING_PASSWORD', stdout=io.StringIO(),
            )
        company = Azienda.objects.get(nome='Azienda già nominata')
        self.assertTrue(company.configurata)
        with azienda_context(company.pk):
            self.assertEqual(AnagraficaAzienda.objects.get().ragione_sociale, company.nome)
        self.assertTrue(self.other.configurata)

    def test_login_next_cannot_skip_first_company_setup(self):
        self.client = Client()
        response = self.client.post(reverse('accounts:login'), {
            'username': self.admin.username,
            'password': self.password,
            'next': reverse('clienti:list'),
        })
        self.assertRedirects(response, self.setup_url, fetch_redirect_response=False)

    def test_ready_company_login_preserves_next(self):
        self.client = Client()
        destination = reverse('settings:utenti')
        response = self.client.post(reverse('accounts:login'), {
            'username': self.other_admin.username, 'password': self.password,
            'next': destination,
        })
        self.assertRedirects(response, destination, fetch_redirect_response=False)

    def test_pending_session_redirects_business_pages_and_admin_to_setup(self):
        for url in (self.home_url, reverse('settings:utenti'), '/admin/', '/media/file.pdf'):
            with self.subTest(url=url):
                self.assertRedirects(
                    self.client.get(url), self.setup_url, fetch_redirect_response=False,
                )

    def test_pending_business_post_does_not_create_an_invited_user(self):
        with patch('core_settings.views._send_invito_email') as send:
            response = self.client.post(reverse('settings:utente_invita'), {
                'email': 'prematuro@example.test', 'first_name': 'Prematuro',
                'ruolo': Ruolo.ACCOUNT_MANAGER,
            })
        self.assertRedirects(response, self.setup_url, fetch_redirect_response=False)
        self.assertFalse(get_user_model().objects.filter(username='prematuro@example.test').exists())
        send.assert_not_called()

    def test_setup_get_shows_empty_company_form_without_foreign_company_data(self):
        response = self.client.get(self.setup_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form']['ragione_sociale'].value(), '')
        self.assertNotContains(response, self.other.nome)
        self.assertNotContains(response, self.other_anagrafica.email)
        self.assert_pending_unchanged()

    def test_valid_setup_updates_only_authenticated_company_and_unlocks_dashboard(self):
        data = self.payload(
            azienda=self.other.pk, azienda_id=self.other.pk,
            id=self.other_anagrafica.pk, pk=self.other_anagrafica.pk,
        )
        response = self.client.post(self.setup_url, data)
        self.assertRedirects(response, self.home_url, fetch_redirect_response=False)
        self.pending.refresh_from_db()
        self.assertTrue(self.pending.configurata)
        self.assertEqual(self.pending.nome, data['ragione_sociale'])
        with azienda_context(self.pending.pk):
            anagrafica = AnagraficaAzienda.objects.get()
            for field, expected in self.payload().items():
                self.assertEqual(getattr(anagrafica, field), expected, field)
            self.assertEqual(Organizzazione.objects.get().nome, data['ragione_sociale'])
        self.other.refresh_from_db()
        self.assertEqual(self.other.nome, 'Azienda esistente riservata')
        with azienda_context(self.other.pk):
            self.assertEqual(AnagraficaAzienda.objects.get().email, 'segreto@esistente.example')
            self.assertEqual(Organizzazione.objects.get().nome, self.other.nome)
        self.assertEqual(self.client.get(self.home_url).status_code, 200)

    def test_company_name_alone_is_enough_to_complete_setup(self):
        response = self.client.post(self.setup_url, {'ragione_sociale': 'Società essenziale'})
        self.assertRedirects(response, self.home_url, fetch_redirect_response=False)
        self.pending.refresh_from_db()
        self.assertTrue(self.pending.configurata)
        self.assertEqual(self.pending.nome, 'Società essenziale')

    def test_invalid_setup_keeps_all_company_data_unchanged(self):
        for changes, field in (
            ({'ragione_sociale': '   '}, 'ragione_sociale'),
            ({'ragione_sociale': 'N' * 201}, 'ragione_sociale'),
            ({'email': 'non-una-email'}, 'email'),
        ):
            with self.subTest(field=field, changes=changes):
                response = self.client.post(self.setup_url, self.payload(**changes))
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context['form'].errors)
                self.assert_pending_unchanged()
                self.assertEqual(
                    Organizzazione.all_objects.get(azienda=self.pending).nome,
                    'Azienda da configurare',
                )

    def test_repeated_setup_post_cannot_overwrite_completed_company(self):
        self.client.post(self.setup_url, self.payload())
        response = self.client.post(self.setup_url, self.payload(ragione_sociale='Sovrascrittura'))
        self.assertRedirects(response, self.home_url, fetch_redirect_response=False)
        self.pending.refresh_from_db()
        self.assertEqual(self.pending.nome, 'Nuova Società SRL')
        self.assertEqual(
            AnagraficaAzienda.all_objects.get(azienda=self.pending).ragione_sociale,
            'Nuova Società SRL',
        )

    def test_existing_company_setup_redirects_without_changing_company(self):
        with azienda_context(self.other.pk):
            self.client.force_login(self.other_admin)
        response = self.client.post(self.setup_url, self.payload())
        self.assertRedirects(response, self.home_url, fetch_redirect_response=False)
        self.other.refresh_from_db()
        self.assertEqual(self.other.nome, 'Azienda esistente riservata')

    def test_only_company_administrators_can_complete_setup(self):
        for role in (Ruolo.VISUALIZZATORE, Ruolo.ACCOUNT_MANAGER):
            with self.subTest(role=role):
                Profile.all_objects.filter(user=self.admin).update(ruolo=role)
                self.assertEqual(self.client.get(self.setup_url).status_code, 403)
                self.assertEqual(self.client.post(self.setup_url, self.payload()).status_code, 403)
                self.assert_pending_unchanged()

    def test_suspended_user_cannot_use_setup_to_restore_access(self):
        Profile.all_objects.filter(user=self.admin).update(stato=StatoUtente.SOSPESO)
        self.assertEqual(self.client.post(self.setup_url, self.payload()).status_code, 403)
        self.assert_pending_unchanged()
        self.client = Client()
        response = self.client.post(reverse('accounts:login'), {
            'username': self.admin.username, 'password': self.password,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].non_field_errors())
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_inactive_company_cannot_use_setup_to_restore_access(self):
        Azienda.objects.filter(pk=self.pending.pk).update(attiva=False)
        self.assertEqual(self.client.post(self.setup_url, self.payload()).status_code, 403)
        self.assert_pending_unchanged()
        self.client = Client()
        response = self.client.post(reverse('accounts:login'), {
            'username': self.admin.username, 'password': self.password,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].non_field_errors())
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_inactive_user_cannot_authenticate_or_complete_setup(self):
        get_user_model().objects.filter(pk=self.admin.pk).update(is_active=False)
        response = self.client.post(self.setup_url, self.payload())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('accounts:login')))
        self.assert_pending_unchanged()
        response = self.client.post(reverse('accounts:login'), {
            'username': self.admin.username, 'password': self.password,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].non_field_errors())

    def test_anonymous_setup_requires_login(self):
        self.client = Client()
        for method in (self.client.get, self.client.post):
            response = method(self.setup_url)
            self.assertRedirects(
                response, reverse('accounts:login') + '?next=' + self.setup_url,
                fetch_redirect_response=False,
            )

    def test_pending_user_can_logout(self):
        response = self.client.post(reverse('accounts:logout'))
        self.assertRedirects(response, reverse('accounts:login'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_pending_company_token_cannot_bypass_setup_via_api_or_mcp(self):
        for client in (self.client, Client()):
            for url in (reverse('api:clienti'), reverse('api:v2_clienti')):
                with self.subTest(session=client is self.client, url=url):
                    response = client.get(url, HTTP_AUTHORIZATION=f'Bearer {self.raw_token}')
                    self.assertEqual(response.status_code, 401)
            response = client.post(
                reverse('mcp'),
                json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}),
                content_type='application/json',
                HTTP_AUTHORIZATION=f'Bearer {self.raw_token}',
            )
            self.assertEqual(response.status_code, 401)
        self.assert_pending_unchanged()

    def test_scheduled_jobs_skip_pending_company_until_setup_is_complete(self):
        visited = []
        with patch('api.models.RichiestaIdempotente.pulisci',
                   side_effect=lambda: visited.append(current_azienda_id()) or 0):
            call_command('pulisci_idempotenza', stdout=io.StringIO())
            self.assertNotIn(self.pending.pk, visited)
            self.assertIn(self.other.pk, visited)
            with self.assertRaises(CommandError):
                call_command('pulisci_idempotenza', azienda=self.pending.pk, stdout=io.StringIO())
            self.client.post(self.setup_url, {'ragione_sociale': 'Azienda pronta'})
            call_command('pulisci_idempotenza', azienda=self.pending.pk, stdout=io.StringIO())
        self.assertEqual(visited[-1], self.pending.pk)


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=['testserver'],
)
class AdminCompanyCreationTests(TestCase):
    password = 'Password-admin-onboarding-2026!'

    @classmethod
    def setUpTestData(cls):
        cls.company = Azienda.objects.create(nome='Azienda amministratore del sito')
        with azienda_context(cls.company.pk):
            cls.superuser = get_user_model().objects.create_superuser(
                username='gestore-sito', password=cls.password,
            )
            cls.staff = get_user_model().objects.create_user(
                username='staff-gestione-utenti', password=cls.password, is_staff=True,
            )
            cls.staff.user_permissions.add(*Permission.objects.filter(
                content_type__app_label='auth',
                codename__in=['add_user', 'change_user', 'view_user'],
            ))

    def setUp(self):
        self.add_url = reverse('admin:auth_user_add')
        with azienda_context(self.company.pk):
            self.client.force_login(self.superuser)

    def payload(self, **changes):
        data = {
            'username': 'amministratore-nuova-azienda',
            'password1': self.password,
            'password2': self.password,
            'usable_password': 'true',
            'destinazione_azienda': 'nuova',
            '_save': 'Salva',
        }
        data.update(changes)
        return data

    def test_superuser_add_form_offers_new_company_with_current_as_default(self):
        response = self.client.get(self.add_url)
        self.assertEqual(response.status_code, 200)
        form = response.context['adminform'].form
        self.assertIn('destinazione_azienda', form.fields)
        self.assertEqual(form['destinazione_azienda'].value(), 'attuale')
        self.assertIn('nuova', dict(form.fields['destinazione_azienda'].choices))

    def test_admin_can_create_first_user_for_new_company_without_company_details(self):
        previous_companies = Azienda.objects.count()
        response = self.client.post(self.add_url, self.payload())
        self.assertRedirects(response, self.add_url, fetch_redirect_response=False)
        self.assertEqual(Azienda.objects.count(), previous_companies + 1)
        user = get_user_model().objects.get(username='amministratore-nuova-azienda')
        profile = Profile.all_objects.select_related('azienda').get(user=user)
        self.assertNotEqual(profile.azienda_id, self.company.pk)
        self.assertFalse(profile.azienda.configurata)
        self.assertEqual(profile.ruolo, Ruolo.AMMINISTRATORE)
        self.assertEqual(profile.stato, StatoUtente.ATTIVO)
        self.assertTrue(user.check_password(self.password))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        with azienda_context(profile.azienda_id):
            self.assertEqual(AnagraficaAzienda.objects.get().ragione_sociale, '')
            self.assertEqual(Organizzazione.objects.get().nome, profile.azienda.nome)
        # Il nuovo utente non entra nella lista utenti dell'azienda del gestore.
        response = self.client.get(reverse('admin:auth_user_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(user.pk, [listed.pk for listed in response.context['cl'].result_list])
        response = self.client.get(reverse('admin:auth_user_change', args=[user.pk]))
        self.assertRedirects(response, reverse('admin:index'), fetch_redirect_response=False)
        other_client = Client()
        response = other_client.post(reverse('accounts:login'), {
            'username': user.username, 'password': self.password,
        })
        self.assertRedirects(
            response, reverse('accounts:configura_azienda'), fetch_redirect_response=False,
        )

    def test_admin_current_company_option_preserves_normal_user_creation(self):
        previous_companies = Azienda.objects.count()
        response = self.client.post(self.add_url, self.payload(destinazione_azienda='attuale'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Azienda.objects.count(), previous_companies)
        user = get_user_model().objects.get(username='amministratore-nuova-azienda')
        self.assertEqual(Profile.all_objects.get(user=user).azienda_id, self.company.pk)

    def test_staff_does_not_see_or_provision_new_company(self):
        with azienda_context(self.company.pk):
            self.client.force_login(self.staff)
        response = self.client.get(self.add_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('destinazione_azienda', response.context['adminform'].form.fields)
        previous_companies = Azienda.objects.count()
        response = self.client.post(self.add_url, self.payload())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Azienda.objects.count(), previous_companies)
        self.assertFalse(get_user_model().objects.filter(username='amministratore-nuova-azienda').exists())
        # Rimuovere il campo per lo staff non deve alterare la form del superuser.
        with azienda_context(self.company.pk):
            self.client.force_login(self.superuser)
        response = self.client.get(self.add_url)
        self.assertIn('destinazione_azienda', response.context['adminform'].form.fields)

    def test_invalid_admin_add_does_not_leave_company_or_user_behind(self):
        previous_companies = Azienda.objects.count()
        previous_users = get_user_model().objects.count()
        cases = (
            {'username': self.superuser.username},
            {'password2': 'password-diversa'},
            {'destinazione_azienda': 'id-altra-azienda'},
            {'usable_password': 'false', 'password1': '', 'password2': ''},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                response = self.client.post(self.add_url, self.payload(**changes))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['adminform'].form.errors)
                self.assertEqual(Azienda.objects.count(), previous_companies)
                self.assertEqual(get_user_model().objects.count(), previous_users)

