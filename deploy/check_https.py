#!/usr/bin/env python3
"""Controlla HTTPS e CSRF del login senza usare credenziali reali.

Uso: python3 deploy/check_https.py https://gestionale.example.it
Richiede solo la libreria standard. Nessun redirect viene seguito e nessuna
risposta, credenziale, cookie o token viene stampato.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from http.cookies import SimpleCookie
import socket
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import (
    HTTPCookieProcessor, HTTPRedirectHandler, HTTPSHandler, Request, build_opener,
)


TIMEOUT_SECONDS = 15
MAX_HTML_BYTES = 1024 * 1024
LOGIN_PATH = '/accounts/login/'
LOGIN_QUERY = 'next=/'
CSRF_COOKIE_NAME = 'aigiscrm_csrftoken'


class CheckError(Exception):
    """Errore con un messaggio sicuro da mostrare nel log di deploy."""


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse potrebbe includere nei suoi errori un argomento contenente
        # credenziali o query string: non ripetere mai l'input ricevuto.
        raise CheckError('Specifica una sola origine HTTPS. Usa --help per la sintassi.')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


@dataclass
class LoginForm:
    method: str
    action: str
    fields: set[str] = field(default_factory=set)
    csrf_tokens: list[str] = field(default_factory=list)


class LoginFormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'form':
            self.current = LoginForm(
                method=(attributes.get('method') or 'get').lower(),
                action=attributes.get('action') or '',
            )
        elif tag == 'input' and self.current is not None and 'disabled' not in attributes:
            name = attributes.get('name')
            kind = (attributes.get('type') or 'text').lower()
            if name == 'username' and kind in {'text', 'email'}:
                self.current.fields.add('username')
            elif name == 'password' and kind == 'password':
                self.current.fields.add('password')
            elif name == 'csrfmiddlewaretoken' and kind == 'hidden':
                self.current.csrf_tokens.append(attributes.get('value') or '')

    def handle_endtag(self, tag):
        if tag == 'form' and self.current is not None:
            self.forms.append(self.current)
            self.current = None


def https_origin(value):
    """Accetta un'origine, senza percorso, credenziali, query o frammento."""
    try:
        if any(character.isspace() or ord(character) < 32 for character in value):
            raise ValueError
        if '\\' in value:
            raise ValueError
        parsed = urlsplit(value)
        if (
            parsed.scheme != 'https' or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
        ):
            raise ValueError
        port = parsed.port
        host = parsed.hostname.encode('idna').decode('ascii').lower()
        if ':' in host:
            host = f'[{host}]'
        authority = host if port in {None, 443} else f'{host}:{port}'
        return f'https://{authority}'
    except (ValueError, UnicodeError):
        raise CheckError(
            'Origine non valida: usa https://nome-host senza credenziali, percorso o parametri.'
        ) from None


def same_https_url(actual, expected, *, compare_query=True):
    """Confronta destinazioni senza accettare credenziali o cambi di origine."""
    try:
        left, right = urlsplit(actual), urlsplit(expected)
        return (
            left.scheme == right.scheme == 'https'
            and left.username is None and left.password is None
            and left.hostname == right.hostname
            and (left.port or 443) == (right.port or 443)
            and left.path == right.path
            and (not compare_query or left.query == right.query)
            and not left.fragment
        )
    except ValueError:
        return False


def open_response(client, request, label):
    try:
        return client.open(request, timeout=TIMEOUT_SECONDS)
    except HTTPError as response:
        # I codici HTTP, inclusi i redirect bloccati, vengono verificati dal
        # chiamante. Non leggere o riportare il body degli errori.
        return response
    except URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            reason = 'certificato TLS non valido o non attendibile'
        elif isinstance(error.reason, (socket.timeout, TimeoutError)):
            reason = 'tempo massimo di connessione superato (15 secondi)'
        elif isinstance(error.reason, ssl.SSLError):
            reason = 'connessione TLS non riuscita'
        else:
            reason = 'connessione non riuscita; verifica DNS, rete e server'
        raise CheckError(f'{label}: {reason}.') from None
    except (socket.timeout, TimeoutError):
        raise CheckError(f'{label}: tempo massimo superato (15 secondi).') from None
    except (OSError, ValueError):
        raise CheckError(f'{label}: connessione non riuscita.') from None


def read_login_form(response, expected_url, label):
    if response.status != 200:
        raise CheckError(f'{label}: atteso HTTP 200, ricevuto HTTP {response.status}.')
    raw = response.read(MAX_HTML_BYTES + 1)
    if len(raw) > MAX_HTML_BYTES:
        raise CheckError(f'{label}: pagina login troppo grande per il controllo.')
    try:
        html = raw.decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
    except LookupError:
        raise CheckError(f'{label}: codifica della pagina non riconosciuta.') from None
    parser = LoginFormParser()
    parser.feed(html)
    parser.close()
    forms = [form for form in parser.forms
             if form.method == 'post' and form.fields == {'username', 'password'}]
    if len(forms) != 1:
        raise CheckError(f'{label}: modulo di accesso non trovato o ambiguo.')
    form = forms[0]
    if len(form.csrf_tokens) != 1 or not form.csrf_tokens[0]:
        raise CheckError(f'{label}: campo CSRF nascosto mancante o ambiguo.')
    if not same_https_url(urljoin(expected_url, form.action), expected_url, compare_query=False):
        raise CheckError(f'{label}: il modulo punta a una destinazione inattesa.')
    return form


def check_https(origin):
    origin = https_origin(origin)
    parsed = urlsplit(origin)
    https_url = urlunsplit(('https', parsed.netloc, LOGIN_PATH, LOGIN_QUERY, ''))
    http_url = urlunsplit(('http', parsed.netloc, LOGIN_PATH, LOGIN_QUERY, ''))
    # Usa la verifica TLS standard di Python: nessuna opzione per disabilitarla.
    context = ssl.create_default_context()
    cookies = CookieJar()
    client = build_opener(
        NoRedirect(), HTTPSHandler(context=context), HTTPCookieProcessor(cookies),
    )
    headers = {'User-Agent': 'GestionaleCRM-HTTPS-Check/1.0'}

    with open_response(client, Request(http_url, headers=headers), 'Redirect HTTP') as response:
        if response.status not in {301, 302, 307, 308}:
            raise CheckError(
                f'Redirect HTTP: atteso passaggio a HTTPS, ricevuto HTTP {response.status}.'
            )
        destination = urljoin(http_url, response.headers.get('Location', ''))
        if not same_https_url(destination, https_url):
            raise CheckError('Redirect HTTP: destinazione HTTPS inattesa; controllo interrotto.')
    print('OK: HTTP reindirizza allo stesso login HTTPS.')

    # Non accettare cookie eventualmente ricevuti dalla risposta HTTP.
    cookies.clear()
    with open_response(client, Request(https_url, headers=headers), 'Login HTTPS') as response:
        form = read_login_form(response, https_url, 'Login HTTPS')
    secure_csrf = [cookie for cookie in cookies if cookie.name == CSRF_COOKIE_NAME
                   and cookie.secure and cookie.value and not cookie.is_expired()]
    if not secure_csrf:
        raise CheckError('Login HTTPS: cookie CSRF Secure assente.')

    post_headers = {
        **headers, 'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': origin, 'Referer': https_url,
    }
    data = urlencode({
        'csrfmiddlewaretoken': form.csrf_tokens[0], 'username': '', 'password': '',
    }).encode('ascii')
    request = Request(https_url, data=data, headers=post_headers, method='POST')
    cookies.add_cookie_header(request)
    attached_cookies = SimpleCookie()
    attached_cookies.load(request.get_header('Cookie', ''))
    if CSRF_COOKIE_NAME not in attached_cookies:
        raise CheckError('Login HTTPS: il cookie CSRF non è valido per il percorso del login.')
    print('OK: certificato HTTPS valido, modulo e cookie CSRF Secure presenti.')

    with open_response(client, request, 'POST con CSRF') as response:
        read_login_form(response, https_url, 'POST con CSRF')
    print('OK: POST con token e cookie accettato; credenziali lasciate vuote.')

    request = Request(
        https_url, data=b'username=&password=', headers=post_headers, method='POST',
    )
    with open_response(client, request, 'POST senza CSRF') as response:
        if response.status != 403:
            raise CheckError(
                f'POST senza CSRF: atteso HTTP 403, ricevuto HTTP {response.status}.'
            )
    print('OK: POST senza token rifiutato con HTTP 403.')
    print('Controllo HTTPS e CSRF completato.')


def main(argv=None):
    parser = SafeArgumentParser(
        description='Verifica HTTPS e CSRF del login senza usare credenziali reali.',
    )
    parser.add_argument('origin', metavar='ORIGINE_HTTPS', help='esempio: https://gestionale.example.it')
    try:
        arguments = parser.parse_args(argv)
        check_https(arguments.origin)
    except CheckError as error:
        print(f'ERRORE: {error}', file=sys.stderr)
        return 1
    except Exception as error:
        # Niente traceback o valori ricevuti dalla rete nei log del deploy.
        print(f'ERRORE: controllo non completato ({type(error).__name__}).', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
