from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile, Ruolo, StatoUtente
from accounts.session_binding import SESSION_AZIENDA_KEY
from anagrafiche.models import AnagraficaAzienda
from api.models import ApiToken
from audit.models import AuditLog, Azione
from clienti.models import Cliente
from core_settings.models import Organizzazione
from tenancy.context import azienda_context
from tenancy.models import Azienda


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=['testserver'],
)
class AdminCompanyAssignmentTests(TestCase):
    password = 'Password-assegnazione-azienda-2026!'

    @classmethod
    def setUpTestData(cls):
        cls.company = Azienda.objects.create(nome='Azienda iniziale assegnazione')
        cls.other_company = Azienda.objects.create(nome='Azienda estranea assegnazione')
        with azienda_context(cls.company.pk):
            cls.superuser = get_user_model().objects.create_superuser(
                username='gestore-assegnazione', password=cls.password,
            )
            cls.staff = get_user_model().objects.create_user(
                username='staff-assegnazione', password=cls.password, is_staff=True,
            )
            cls.staff.user_permissions.add(*Permission.objects.filter(
                content_type__app_label='auth',
                codename__in=['add_user', 'change_user', 'view_user'],
            ))
            cls.user = get_user_model().objects.create_user(
                username='nuovo-assegnazione', password=cls.password,
                email='nuovo@example.test', first_name='Nuovo', last_name='Utente',
            )
            cls.old_client = Cliente.objects.create(ragione_sociale='Cliente riservato originale')
        with azienda_context(cls.other_company.pk):
            cls.other_user = get_user_model().objects.create_user(
                username='estraneo-assegnazione', password=cls.password,
            )

    def setUp(self):
        with azienda_context(self.company.pk):
            self.client.force_login(self.superuser)

    def assignment_url(self, user=None):
        return reverse('admin:auth_user_nuova_azienda', args=[(user or self.user).pk])

    def confirm_assignment(self, user=None, client=None):
        return (client or self.client).post(self.assignment_url(user), {'confirm': 'yes'})

    def assert_original_membership(self, user, previous_companies):
        self.assertEqual(Azienda.objects.count(), previous_companies)
        self.assertEqual(Profile.all_objects.get(user=user).azienda_id, self.company.pk)

    def test_confirmation_get_does_not_change_company_or_credentials(self):
        previous_companies = Azienda.objects.count()
        previous_hash = self.user.password
        response = self.client.get(self.assignment_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user.username)
        self.assertContains(response, 'csrfmiddlewaretoken')
        self.assert_original_membership(self.user, previous_companies)
        self.user.refresh_from_db()
        self.assertEqual(self.user.password, previous_hash)

    def test_assignment_preserves_credentials_and_starts_isolated_company_setup(self):
        previous_companies = Azienda.objects.count()
        previous_hash = self.user.password
        previous_profile_id = Profile.all_objects.get(user=self.user).pk
        response = self.confirm_assignment()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Azienda.objects.count(), previous_companies + 1)
        self.user.refresh_from_db()
        profile = Profile.all_objects.select_related('azienda').get(user=self.user)
        self.assertEqual(self.user.username, 'nuovo-assegnazione')
        self.assertEqual(self.user.email, 'nuovo@example.test')
        self.assertEqual(self.user.first_name, 'Nuovo')
        self.assertEqual(self.user.last_name, 'Utente')
        self.assertEqual(self.user.password, previous_hash)
        self.assertTrue(self.user.check_password(self.password))
        self.assertEqual(profile.pk, previous_profile_id)
        self.assertNotEqual(profile.azienda_id, self.company.pk)
        self.assertTrue(profile.azienda.attiva)
        self.assertFalse(profile.azienda.configurata)
        self.assertEqual(profile.ruolo, Ruolo.AMMINISTRATORE)
        self.assertEqual(profile.stato, StatoUtente.ATTIVO)
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)
        with azienda_context(profile.azienda_id):
            self.assertEqual(AnagraficaAzienda.objects.get().ragione_sociale, '')
            self.assertEqual(Organizzazione.objects.get().nome, profile.azienda.nome)
            self.assertFalse(Cliente.objects.exists())
            self.assertFalse(ApiToken.objects.exists())
        with azienda_context(self.company.pk):
            self.assertTrue(Cliente.objects.filter(pk=self.old_client.pk).exists())
        listing = self.client.get(reverse('admin:auth_user_changelist'))
        self.assertNotIn(self.user.pk, [user.pk for user in listing.context['cl'].result_list])
        login = Client().post(reverse('accounts:login'), {
            'username': self.user.username, 'password': self.password,
        })
        self.assertRedirects(
            login, reverse('accounts:configura_azienda'), fetch_redirect_response=False,
        )

    def test_existing_account_usage_and_privileges_prevent_assignment(self):
        blockers = (
            'staff', 'superuser', 'inactive', 'unusable_password',
            'avatar', 'api_token', 'client_assignment', 'audit_actor',
            'group', 'permission', 'profile_suspended',
            'profile_invited', 'profile_phone',
        )
        for blocker in blockers:
            with self.subTest(blocker=blocker), azienda_context(self.company.pk):
                user = get_user_model().objects.create_user(
                    username=f'non-assegnabile-{blocker}', password=self.password,
                )
                if blocker == 'staff':
                    user.is_staff = True
                    user.save(update_fields=['is_staff'])
                elif blocker == 'superuser':
                    user.is_superuser = True
                    user.save(update_fields=['is_superuser'])
                elif blocker == 'inactive':
                    user.is_active = False
                    user.save(update_fields=['is_active'])
                elif blocker == 'unusable_password':
                    user.set_unusable_password()
                    user.save(update_fields=['password'])
                elif blocker == 'avatar':
                    user.profile.avatar = 'avatars/allegato-azienda-iniziale.png'
                    user.profile.save(update_fields=['avatar'])
                elif blocker == 'api_token':
                    ApiToken.objects.create(user=user, name='Token azienda iniziale')
                elif blocker == 'client_assignment':
                    Cliente.objects.create(ragione_sociale='Cliente assegnato', account_manager=user)
                elif blocker == 'audit_actor':
                    AuditLog.objects.create(actor=user, azione=Azione.UPDATE)
                elif blocker == 'group':
                    user.groups.add(Group.objects.create(name='Gruppo non trasferibile'))
                elif blocker == 'permission':
                    user.user_permissions.add(Permission.objects.get(
                        content_type__app_label='auth', codename='change_user',
                    ))
                elif blocker == 'profile_suspended':
                    user.profile.stato = StatoUtente.SOSPESO
                    user.profile.save(update_fields=['stato'])
                elif blocker == 'profile_invited':
                    user.profile.stato = StatoUtente.INVITATO
                    user.profile.save(update_fields=['stato'])
                elif blocker == 'profile_phone':
                    user.profile.telefono = '0200000000'
                    user.profile.save(update_fields=['telefono'])
                previous_companies = Azienda.objects.count()
                previous_hash = user.password
                response = self.confirm_assignment(user)
                self.assertEqual(response.status_code, 200)
                self.assert_original_membership(user, previous_companies)
                user.refresh_from_db()
                self.assertEqual(user.password, previous_hash)

    def test_superuser_cannot_assign_self(self):
        previous_companies = Azienda.objects.count()
        response = self.confirm_assignment(self.superuser)
        self.assertEqual(response.status_code, 200)
        self.assert_original_membership(self.superuser, previous_companies)

    def test_staff_cannot_get_or_post_assignment_even_with_user_change_permission(self):
        with azienda_context(self.company.pk):
            self.client.force_login(self.staff)
        previous_companies = Azienda.objects.count()
        for method in (self.client.get, self.client.post):
            with self.subTest(method=method.__name__):
                response = method(self.assignment_url(), {'confirm': 'yes'})
                self.assertEqual(response.status_code, 403)
                self.assert_original_membership(self.user, previous_companies)

    def test_foreign_account_is_not_visible_or_assignable(self):
        previous_companies = Azienda.objects.count()
        for method in (self.client.get, self.client.post):
            with self.subTest(method=method.__name__):
                response = method(self.assignment_url(self.other_user), {'confirm': 'yes'})
                self.assertEqual(response.status_code, 404)
                self.assertNotContains(response, self.other_user.username, status_code=response.status_code)
                self.assertEqual(Azienda.objects.count(), previous_companies)
                self.assertEqual(
                    Profile.all_objects.get(user=self.other_user).azienda_id, self.other_company.pk,
                )

    def test_login_after_confirmation_is_allowed_and_its_session_is_revoked(self):
        self.assertEqual(self.client.get(self.assignment_url()).status_code, 200)
        target_client = Client()
        login = target_client.post(reverse('accounts:login'), {
            'username': self.user.username, 'password': self.password,
        })
        self.assertEqual(login.status_code, 302)
        session_key = target_client.session.session_key
        previous_companies = Azienda.objects.count()
        response = self.confirm_assignment()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Azienda.objects.count(), previous_companies + 1)
        self.assertNotEqual(Profile.all_objects.get(user=self.user).azienda_id, self.company.pk)
        self.assertFalse(Session.objects.filter(pk=session_key).exists())

    def test_business_event_after_confirmation_prevents_assignment(self):
        self.assertEqual(self.client.get(self.assignment_url()).status_code, 200)
        with azienda_context(self.company.pk):
            AuditLog.objects.create(
                actor=self.user, azione=Azione.EXPORT, target_type='Cliente',
                target_id=str(self.old_client.pk),
            )
        previous_companies = Azienda.objects.count()
        response = self.confirm_assignment()
        self.assertEqual(response.status_code, 200)
        self.assert_original_membership(self.user, previous_companies)

    def test_assignment_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        with azienda_context(self.company.pk):
            client.force_login(self.superuser)
        previous_companies = Azienda.objects.count()
        response = self.confirm_assignment(client=client)
        self.assertEqual(response.status_code, 403)
        self.assert_original_membership(self.user, previous_companies)

    def test_assignment_requires_explicit_confirmation(self):
        previous_companies = Azienda.objects.count()
        response = self.client.post(self.assignment_url())
        self.assertEqual(response.status_code, 400)
        self.assert_original_membership(self.user, previous_companies)

    def test_setup_failure_rolls_back_company_profile_and_audit_changes(self):
        target_client = Client()
        target_client.post(reverse('accounts:login'), {
            'username': self.user.username, 'password': self.password,
        })
        session_key = target_client.session.session_key
        previous_companies = Azienda.objects.count()
        previous_details = AnagraficaAzienda.all_objects.count()
        previous_audit_logs = AuditLog.all_objects.count()
        previous_hash = self.user.password
        previous_role = Profile.all_objects.get(user=self.user).ruolo
        previous_auth_history = list(AuditLog.all_objects.filter(actor=self.user).values())
        with patch(
            'accounts.company_assignment.Organizzazione.objects.create',
            side_effect=RuntimeError('Errore simulato inizializzazione azienda'),
        ), self.assertRaises(RuntimeError):
            self.confirm_assignment()
        self.assert_original_membership(self.user, previous_companies)
        self.assertEqual(AnagraficaAzienda.all_objects.count(), previous_details)
        self.assertEqual(AuditLog.all_objects.count(), previous_audit_logs)
        self.assertEqual(Profile.all_objects.get(user=self.user).ruolo, previous_role)
        self.assertTrue(Session.objects.filter(pk=session_key).exists())
        self.assertEqual(
            list(AuditLog.all_objects.filter(actor=self.user).values()), previous_auth_history,
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.password, previous_hash)

    def test_login_history_stays_in_original_company_and_only_target_sessions_are_revoked(self):
        target_client = Client()
        legacy_client = Client()
        for client in (target_client, legacy_client):
            self.assertEqual(client.post(reverse('accounts:login'), {
                'username': self.user.username, 'password': self.password,
            }).status_code, 302)
        self.assertEqual(target_client.session[SESSION_AZIENDA_KEY], self.company.pk)
        legacy_session = legacy_client.session
        legacy_session.pop(SESSION_AZIENDA_KEY)
        legacy_session.save()
        old_session_keys = [target_client.session.session_key, legacy_client.session.session_key]
        other_session_key = self.client.session.session_key
        with azienda_context(self.company.pk):
            AuditLog.objects.create(
                actor=self.user, actor_label='', azione=Azione.LOGOUT,
                target_type='User', target_id=str(self.user.pk),
                ip='192.0.2.1', meta={'test': 'storia da conservare'},
            )
            ultimo_accesso = timezone.now()
            Profile.objects.filter(user=self.user).update(ultimo_accesso=ultimo_accesso)
        previous_history = list(AuditLog.all_objects.filter(actor=self.user).values())
        self.user.refresh_from_db()
        last_login = self.user.last_login
        response = self.confirm_assignment()
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Session.objects.filter(pk__in=old_session_keys).exists())
        self.assertTrue(Session.objects.filter(pk=other_session_key).exists())
        self.assertEqual(self.client.get(reverse('admin:index')).status_code, 200)
        for old_event in previous_history:
            saved_event = AuditLog.all_objects.values().get(pk=old_event['id'])
            self.assertEqual(saved_event['actor_id'], None)
            self.assertEqual(saved_event['actor_label'], old_event['actor_label'] or self.user.username)
            for key in old_event.keys() - {'actor_id', 'actor_label'}:
                self.assertEqual(saved_event[key], old_event[key], key)
        self.user.refresh_from_db()
        self.assertEqual(self.user.last_login, last_login)
        self.assertEqual(Profile.all_objects.get(user=self.user).ultimo_accesso, ultimo_accesso)
        for old_client in (target_client, legacy_client):
            response = old_client.get(reverse('accounts:configura_azienda'))
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(reverse('accounts:login')))
            self.assertNotIn('_auth_user_id', old_client.session)

    def test_audit_allowlist_rejects_wrong_target_action_and_company(self):
        variants = (
            {'target_type': 'Cliente'},
            {'target_id': str(self.superuser.pk)},
            {'azione': Azione.EXPORT},
            {'azienda_id': self.other_company.pk},
        )
        for index, changes in enumerate(variants):
            with self.subTest(changes=changes), azienda_context(self.company.pk):
                user = get_user_model().objects.create_user(
                    username=f'audit-non-trasferibile-{index}', password=self.password,
                )
                values = {
                    'actor': user, 'azione': Azione.LOGIN,
                    'target_type': 'User', 'target_id': str(user.pk),
                }
                values.update(changes)
                # Simulate a historical cross-company reference: normal model
                # saves correctly forbid creating one under another tenant.
                foreign_company_id = values.pop('azienda_id', None)
                event = AuditLog.objects.create(**values)
                if foreign_company_id:
                    AuditLog.all_objects.filter(pk=event.pk).update(azienda_id=foreign_company_id)
                previous_companies = Azienda.objects.count()
                response = self.confirm_assignment(user)
                self.assertEqual(response.status_code, 200)
                self.assert_original_membership(user, previous_companies)
                self.assertEqual(AuditLog.all_objects.get(pk=event.pk).actor_id, user.pk)

    @override_settings(SESSION_ENGINE='django.contrib.sessions.backends.signed_cookies')
    def test_unsupported_session_backend_prevents_assignment(self):
        # Use the service directly because the existing admin login cookie was
        # created using the normal database backend.
        from django.core.exceptions import ValidationError
        from accounts.company_assignment import prepara_azienda_per_utente_esistente

        previous_companies = Azienda.objects.count()
        with azienda_context(self.company.pk), self.assertRaises(ValidationError):
            prepara_azienda_per_utente_esistente(self.user, self.superuser)
        self.assert_original_membership(self.user, previous_companies)
