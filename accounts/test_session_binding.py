import json

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import Profile, Ruolo
from accounts.session_binding import SESSION_AZIENDA_KEY
from api.models import ApiToken
from clienti.models import Cliente
from tenancy.context import azienda_context
from tenancy.models import Azienda


@override_settings(
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    SECURE_SSL_REDIRECT=False,
    ALLOWED_HOSTS=['testserver'],
)
class CompanySessionBindingTests(TestCase):
    password = 'Password-session-company-2026!'

    @classmethod
    def setUpTestData(cls):
        cls.company = Azienda.objects.create(nome='Azienda della sessione')
        cls.other_company = Azienda.objects.create(nome='Azienda del token')
        with azienda_context(cls.company.pk):
            cls.user = get_user_model().objects.create_user(
                username='session-company', password=cls.password,
            )
            cls.user.profile.ruolo = Ruolo.AMMINISTRATORE
            cls.user.profile.save(update_fields=['ruolo'])
            Cliente.objects.create(ragione_sociale='Cliente privato sessione')
        with azienda_context(cls.other_company.pk):
            token_user = get_user_model().objects.create_user(
                username='token-company', password=cls.password,
            )
            token = ApiToken.objects.create(user=token_user, name='Token indipendente')
            cls.raw_token = token.token_in_chiaro
            Cliente.objects.create(ragione_sociale='Cliente visibile token')

    def login_client(self):
        client = Client()
        response = client.post(reverse('accounts:login'), {
            'username': self.user.username, 'password': self.password,
        })
        self.assertEqual(response.status_code, 302)
        return client

    def test_login_binds_session_to_authenticated_company(self):
        client = self.login_client()
        self.assertEqual(client.session[SESSION_AZIENDA_KEY], self.company.pk)
        response = client.get(reverse('accounts:configura_azienda'))
        self.assertRedirects(response, reverse('dashboard:home'), fetch_redirect_response=False)
        self.assertEqual(client.session['_auth_user_id'], str(self.user.pk))

    def test_legacy_session_remains_valid_without_company_binding(self):
        client = self.login_client()
        session = client.session
        session.pop(SESSION_AZIENDA_KEY)
        session.save()
        response = client.get(reverse('accounts:configura_azienda'))
        self.assertRedirects(response, reverse('dashboard:home'), fetch_redirect_response=False)
        self.assertEqual(client.session['_auth_user_id'], str(self.user.pk))

    def test_stale_session_is_logged_out_after_membership_changes(self):
        client = self.login_client()
        old_session_key = client.session.session_key
        Profile.all_objects.filter(user=self.user).update(azienda_id=self.other_company.pk)
        response = client.get(reverse('accounts:configura_azienda'))
        self.assertRedirects(response, reverse('accounts:login'), fetch_redirect_response=False)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertIsNone(response.wsgi_request.azienda_id)
        self.assertNotIn('_auth_user_id', client.session)
        self.assertFalse(Session.objects.filter(pk=old_session_key).exists())

    def test_stale_htmx_session_requests_full_login_redirect(self):
        client = self.login_client()
        session = client.session
        session[SESSION_AZIENDA_KEY] = self.other_company.pk
        session.save()
        response = client.get(reverse('accounts:configura_azienda'), HTTP_HX_REQUEST='true')
        self.assertRedirects(response, reverse('accounts:login'), fetch_redirect_response=False)
        self.assertEqual(response['HX-Redirect'], reverse('accounts:login'))
        self.assertNotIn('_auth_user_id', client.session)

    def test_api_token_authentication_is_independent_of_stale_browser_session(self):
        client = self.login_client()
        session = client.session
        session[SESSION_AZIENDA_KEY] = self.other_company.pk
        session.save()
        response = client.get(
            reverse('api:clienti'), HTTP_AUTHORIZATION=f'Bearer {self.raw_token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cliente visibile token')
        self.assertNotContains(response, 'Cliente privato sessione')
        self.assertNotIn('_auth_user_id', client.session)

    def test_mcp_token_authentication_is_independent_of_stale_browser_session(self):
        client = self.login_client()
        session = client.session
        session[SESSION_AZIENDA_KEY] = self.other_company.pk
        session.save()
        response = client.post(
            reverse('mcp'), json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}),
            content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {self.raw_token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('result', response.json())
        self.assertNotIn('_auth_user_id', client.session)

    def test_stale_session_without_api_token_does_not_authorize_api(self):
        client = self.login_client()
        session = client.session
        session[SESSION_AZIENDA_KEY] = self.other_company.pk
        session.save()
        response = client.get(reverse('api:clienti'))
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('_auth_user_id', client.session)
