from html.parser import HTMLParser
from unittest.mock import patch

from django.conf import settings
from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse


class _CSRFTokenParser(HTMLParser):
    token = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('name') == 'csrfmiddlewaretoken':
            self.token = attrs.get('value')


@override_settings(
    ALLOWED_HOSTS=['crm.example.test'],
    CSRF_TRUSTED_ORIGINS=[],
    CSRF_COOKIE_SECURE=True,
    CSRF_COOKIE_DOMAIN=None,
    CSRF_USE_SESSIONS=False,
    SESSION_COOKIE_SECURE=True,
    SECURE_SSL_REDIRECT=True,
    SECURE_SSL_HOST=None,
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
)
class LoginCSRFTests(SimpleTestCase):
    """Esercita middleware e form reali senza bypassare le verifiche CSRF.

    Il client Django standard disabilita questi controlli. Qui il backend di
    autenticazione restituisce sempre credenziali invalide e il branding non
    accede all'anagrafica: non servono account né accessi al database.
    """

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True, HTTP_HOST='crm.example.test')
        self.login_url = reverse('accounts:login')
        branding = patch('core_settings.context_processors._azienda', return_value=None)
        branding.start()
        self.addCleanup(branding.stop)
        authenticate = patch('django.contrib.auth.forms.authenticate', return_value=None)
        self.authenticate = authenticate.start()
        self.addCleanup(authenticate.stop)

    def login_form(self):
        response = self.client.get(self.login_url, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'registration/login.html')
        parser = _CSRFTokenParser()
        parser.feed(response.content.decode())
        self.assertTrue(parser.token, 'Il form deve includere il token CSRF.')
        self.assertIn(settings.CSRF_COOKIE_NAME, response.cookies)
        return response, parser.token

    def credentials(self, token):
        return {
            'username': 'utente-inesistente-csrf',
            'password': 'credenziali-di-test-non-valide',
            'csrfmiddlewaretoken': token,
        }

    def test_https_get_emits_secure_cookie_and_hidden_csrf_token(self):
        response, token = self.login_form()
        cookie = response.cookies[settings.CSRF_COOKIE_NAME]
        self.assertTrue(cookie['secure'])
        self.assertTrue(cookie.value)
        self.assertRegex(token, r'^[a-zA-Z0-9]+$')
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.authenticate.assert_not_called()

    def test_https_post_with_cookie_token_and_same_origin_reaches_authentication(self):
        _, token = self.login_form()
        response = self.client.post(
            self.login_url, self.credentials(token), secure=True,
            HTTP_ORIGIN='https://crm.example.test',
        )
        # 200 con errore credenziali prova che il POST ha superato CSRF; non
        # basta controllare che la pagina di login sia raggiungibile in GET.
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'registration/login.html')
        self.assertTrue(response.context['form'].non_field_errors())
        self.authenticate.assert_called_once_with(
            response.wsgi_request,
            username='utente-inesistente-csrf',
            password='credenziali-di-test-non-valide',
        )
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertNotIn('_auth_user_id', response.wsgi_request.session)

    def test_https_post_without_cookie_is_rejected_before_authentication(self):
        _, token = self.login_form()
        del self.client.cookies[settings.CSRF_COOKIE_NAME]
        response = self.client.post(
            self.login_url, self.credentials(token), secure=True,
            HTTP_ORIGIN='https://crm.example.test',
        )
        self.assertEqual(response.status_code, 403)
        self.authenticate.assert_not_called()

    def test_https_post_from_foreign_origin_is_rejected_even_with_valid_cookie_and_token(self):
        _, token = self.login_form()
        response = self.client.post(
            self.login_url, self.credentials(token), secure=True,
            HTTP_ORIGIN='https://untrusted.example.test',
        )
        self.assertEqual(response.status_code, 403)
        self.authenticate.assert_not_called()

    def test_http_is_redirected_to_https_before_rendering_login_or_issuing_cookie(self):
        response = self.client.get(self.login_url + '?next=%2Fclienti%2F')
        self.assertRedirects(
            response,
            'https://crm.example.test' + self.login_url + '?next=%2Fclienti%2F',
            status_code=301, fetch_redirect_response=False,
        )
        self.assertTemplateNotUsed(response, 'registration/login.html')
        self.assertNotIn(settings.CSRF_COOKIE_NAME, response.cookies)
        self.assertNotContains(response, 'csrfmiddlewaretoken', status_code=301)
        self.authenticate.assert_not_called()

    def test_trusted_https_proxy_header_serves_form_without_redirect_loop(self):
        response = self.client.get(self.login_url, HTTP_X_FORWARDED_PROTO='https')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.wsgi_request.is_secure())
        self.assertTemplateUsed(response, 'registration/login.html')
        self.assertTrue(response.cookies[settings.CSRF_COOKIE_NAME]['secure'])
