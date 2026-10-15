import io
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from accounts.models import Profile, Ruolo, StatoUtente
from accounts.tenancy import utenti_azienda
from accounts.views import AziendaAuthenticationForm
from anagrafiche.models import AnagraficaAzienda
from api.models import ApiToken
from attivita.forms import AttivitaForm
from attivita.models import Attivita
from cashflow.forms import RimborsoChilometricoForm
from clienti.forms import ClienteForm
from clienti.models import Cliente
from core_settings.models import Organizzazione
from opportunita.forms import OpportunitaForm

from .context import azienda_context, current_azienda_id
from .models import Azienda


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=['testserver'],
)
class TenantAccountTests(TestCase):
    password = 'Password-test-aziende-2026!'

    @classmethod
    def setUpTestData(cls):
        cls.azienda_a = Azienda.objects.create(nome='Azienda Account A')
        cls.azienda_b = Azienda.objects.create(nome='Azienda Account B')
        with azienda_context(cls.azienda_a.pk):
            cls.user_a = get_user_model().objects.create_user(
                username='admin-azienda-a', password=cls.password,
                first_name='Amministratore Alfa', is_staff=True, is_superuser=True,
            )
            cls.user_a.profile.ruolo = Ruolo.AMMINISTRATORE
            cls.user_a.profile.save()
        with azienda_context(cls.azienda_b.pk):
            cls.user_b = get_user_model().objects.create_user(
                username='utente-azienda-b', password=cls.password,
                first_name='Persona Riservata Beta',
            )

    def setUp(self):
        with azienda_context(self.azienda_a.pk):
            self.client.force_login(self.user_a)

    def test_login_resolves_membership_outside_current_company(self):
        with azienda_context(None):
            form = AziendaAuthenticationForm(data={
                'username': self.user_b.username, 'password': self.password,
            })
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.get_user().pk, self.user_b.pk)

    def test_existing_membership_cannot_be_assigned_to_another_user(self):
        with azienda_context(self.azienda_a.pk):
            profile = Profile.objects.get(user=self.user_a)
            profile.user = self.user_b
            with self.assertRaises(PermissionDenied):
                profile.save()
            profile.refresh_from_db()
            self.assertEqual(profile.user_id, self.user_a.pk)

    def test_login_rejects_disabled_company_and_missing_membership(self):
        Azienda.objects.filter(pk=self.azienda_b.pk).update(attiva=False)
        with azienda_context(None):
            form = AziendaAuthenticationForm(data={
                'username': self.user_b.username, 'password': self.password,
            })
            self.assertFalse(form.is_valid())
            self.assertEqual(form.non_field_errors().as_data()[0].code, 'azienda_non_disponibile')
        Profile.all_objects.filter(user=self.user_b).delete()
        with azienda_context(None):
            form = AziendaAuthenticationForm(data={
                'username': self.user_b.username, 'password': self.password,
            })
            self.assertFalse(form.is_valid())

    def test_login_rejects_suspended_profile_but_accepts_invited_user(self):
        with azienda_context(self.azienda_b.pk):
            profile = Profile.objects.get(user=self.user_b)
            profile.stato = StatoUtente.SOSPESO
            profile.save(update_fields=['stato'])
        with azienda_context(None):
            form = AziendaAuthenticationForm(data={
                'username': self.user_b.username, 'password': self.password,
            })
            self.assertFalse(form.is_valid())
        with azienda_context(self.azienda_b.pk):
            profile.stato = StatoUtente.INVITATO
            profile.save(update_fields=['stato'])
        with azienda_context(None):
            form = AziendaAuthenticationForm(data={
                'username': self.user_b.username, 'password': self.password,
            })
            self.assertTrue(form.is_valid(), form.errors)

    def test_superuser_lists_only_company_users_and_team(self):
        for route in ('settings:utenti', 'team:index'):
            response = self.client.get(reverse(route))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, self.user_a.first_name)
            self.assertNotContains(response, self.user_b.first_name)

    def test_foreign_user_actions_and_team_are_not_accessible(self):
        routes = (
            ('settings:utente_cambia_ruolo', {'ruolo': Ruolo.AMMINISTRATORE}),
            ('settings:utente_toggle_attivo', {}),
            ('settings:utente_elimina', {}),
            ('team:modifica', {'qualifica': 'Modifica non consentita'}),
        )
        for route, data in routes:
            response = self.client.post(reverse(route, args=[self.user_b.pk]), data)
            self.assertEqual(response.status_code, 404, route)
        response = self.client.get(
            reverse('team:scheda', args=[self.user_b.pk]), HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 404)
        self.user_b.refresh_from_db()
        self.assertTrue(self.user_b.is_active)
        self.assertEqual(Profile.all_objects.get(user=self.user_b).ruolo, Ruolo.ACCOUNT_MANAGER)

    def test_forms_reject_foreign_user_ids(self):
        request = RequestFactory().get('/')
        request.user = self.user_a
        with azienda_context(self.azienda_a.pk):
            cases = (
                (ClienteForm(), 'account_manager'),
                (AttivitaForm(), 'owner'),
                (OpportunitaForm(), 'owner'),
                (RimborsoChilometricoForm(request=request), 'amministratore'),
            )
            for form, name in cases:
                with self.subTest(form=type(form).__name__):
                    self.assertIn(self.user_a, form.fields[name].queryset)
                    self.assertNotIn(self.user_b, form.fields[name].queryset)
                    with self.assertRaises(ValidationError):
                        form.fields[name].clean(self.user_b.pk)
        with azienda_context(None):
            self.assertFalse(utenti_azienda().exists())

    def test_admin_user_choices_and_filters_are_scoped(self):
        request = RequestFactory().get('/admin/')
        request.user = self.user_a
        with azienda_context(self.azienda_a.pk):
            users_admin = admin.site._registry[get_user_model()]
            self.assertEqual(list(users_admin.get_queryset(request)), [self.user_a])
            token_admin = admin.site._registry[ApiToken]
            field = token_admin.formfield_for_foreignkey(ApiToken._meta.get_field('user'), request)
            self.assertEqual(list(field.queryset), [self.user_a])
            activity_admin = admin.site._registry[Attivita]
            filter_entry = next(f for f in activity_admin.get_list_filter(request) if isinstance(f, tuple))
            filter_type = filter_entry[1]
            instance = filter_type(Attivita._meta.get_field('owner'), request, {}, Attivita, activity_admin, 'owner')
            self.assertEqual(instance.lookup_choices, [(self.user_a.pk, str(self.user_a))])
        self.assertNotIn(Group, admin.site._registry)

    def test_global_backup_is_unavailable_even_to_superuser(self):
        with patch('core_settings.backup.crea') as create, patch('core_settings.backup.elenco') as listing:
            response = self.client.get(reverse('settings:backup'))
            self.assertContains(response, 'amministratore del server')
            self.assertEqual(self.client.post(reverse('settings:backup')).status_code, 403)
            response = self.client.get(reverse('settings:backup_scarica', args=['backup.zip']))
            self.assertEqual(response.status_code, 404)
            create.assert_not_called()
            listing.assert_not_called()

    def test_invitation_stays_in_current_company(self):
        with patch('core_settings.views._send_invito_email', return_value=True) as send:
            response = self.client.post(reverse('settings:utente_invita'), {
                'email': 'nuovo@azienda-a.example', 'first_name': 'Nuovo',
                'ruolo': Ruolo.ACCOUNT_MANAGER,
            })
        self.assertEqual(response.status_code, 302)
        send.assert_called_once()
        user = get_user_model().objects.get(username='nuovo@azienda-a.example')
        self.assertEqual(Profile.all_objects.get(user=user).azienda_id, self.azienda_a.pk)

    def test_command_creates_company_admin_without_disclosing_password(self):
        output = io.StringIO()
        before = current_azienda_id()
        with patch.dict('os.environ', {'TEST_AZIENDA_PASSWORD': self.password}):
            call_command(
                'crea_azienda', 'Nuova Azienda Test', username='nuovo-admin-test',
                email='admin@nuova.example', password_env='TEST_AZIENDA_PASSWORD', stdout=output,
            )
        company = Azienda.objects.get(nome='Nuova Azienda Test')
        user = get_user_model().objects.get(username='nuovo-admin-test')
        profile = Profile.all_objects.get(user=user)
        self.assertEqual(profile.azienda_id, company.pk)
        self.assertEqual(profile.ruolo, Ruolo.AMMINISTRATORE)
        self.assertTrue(user.check_password(self.password))
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
        with azienda_context(company.pk):
            self.assertEqual(Organizzazione.current().nome, company.nome)
            self.assertEqual(AnagraficaAzienda.current().ragione_sociale, company.nome)
        self.assertNotIn(self.password, output.getvalue())
        self.assertEqual(current_azienda_id(), before)

    def test_command_rejects_duplicate_username_without_new_company(self):
        before = Azienda.objects.count()
        with self.assertRaises(CommandError):
            call_command('crea_azienda', 'Da Non Creare', username=self.user_b.username, stdout=io.StringIO())
        self.assertEqual(Azienda.objects.count(), before)

    def test_demo_seed_and_reset_do_not_touch_another_company(self):
        with override_settings(DEBUG=True):
            call_command('seed', azienda=self.azienda_b.pk, stdout=io.StringIO())
            with azienda_context(self.azienda_b.pk):
                expected_clients = list(Cliente.objects.values_list('pk', flat=True))
                expected_users = list(utenti_azienda().values_list('pk', flat=True))
            output = io.StringIO()
            call_command('seed', azienda=self.azienda_a.pk, reset=True, stdout=output)
        with azienda_context(self.azienda_b.pk):
            self.assertEqual(list(Cliente.objects.values_list('pk', flat=True)), expected_clients)
            self.assertEqual(list(utenti_azienda().values_list('pk', flat=True)), expected_users)
        self.assertIn(f'azienda{self.azienda_a.pk}.marco@studiorossi.it', output.getvalue())
