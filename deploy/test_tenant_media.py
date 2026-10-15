"""Il gate usa solo HTTP simulato: nessuna richiesta al sito reale."""
from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from .check_https import CheckError
from .check_tenant_media import check_tenant_media, main


class Response(BytesIO):
    def __init__(self, status, body=b'', headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}


class TenantMediaDeployTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.requests = []
        patcher = patch('deploy.check_tenant_media.build_opener')
        self.client = patcher.start().return_value
        self.addCleanup(patcher.stop)
        self.output = StringIO()
        stdout = redirect_stdout(self.output)
        stdout.__enter__()
        self.addCleanup(stdout.__exit__, None, None, None)

    def check(self):
        check_tenant_media('https://gestionale.example.test', self.root)

    def assert_clean(self):
        self.assertEqual(list(self.root.rglob('*.txt')), [])

    def test_file_pubblico_blocca_deploy_e_rimuove_canary(self):
        def serve(request, timeout):
            self.requests.append(request)
            relative = urlsplit(request.full_url).path.removeprefix('/media/')
            payload = (self.root / relative).read_bytes()
            return Response(200, payload)
        self.client.open.side_effect = serve
        with self.assertRaisesRegex(CheckError, 'ancora pubblici'):
            self.check()
        self.assert_clean()
        self.assertEqual(len(self.requests), 1)
        self.assertIsNone(self.requests[0].get_header('Cookie'))

    def test_login_e_404_sono_protetti(self):
        self.client.open.side_effect = [
            HTTPError('https://gestionale.example.test/media/test', 302, 'Found',
                      {'Location': '/accounts/login/?next=/media/test'}, None),
            HTTPError('https://gestionale.example.test/media/test', 404, 'Not Found', {}, None),
        ]
        self.check()
        self.assertEqual(self.client.open.call_count, 2)
        self.assert_clean()
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertIn('OK:', self.output.getvalue())

    def test_umask_restrittiva_non_maschera_file_pubblici(self):
        def serve(request, timeout):
            relative = urlsplit(request.full_url).path.removeprefix('/media/')
            target = self.root / relative
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            self.assertEqual(target.parent.stat().st_mode & 0o777, 0o755)
            return Response(200, target.read_bytes())
        self.client.open.side_effect = serve
        previous_umask = os.umask(0o077)
        try:
            with self.assertRaisesRegex(CheckError, 'ancora pubblici'):
                self.check()
        finally:
            os.umask(previous_umask)
        self.assert_clean()

    def test_avatar_legacy_pubblico_blocca_anche_se_prefisso_protetto(self):
        def serve(request, timeout):
            relative = urlsplit(request.full_url).path.removeprefix('/media/')
            if relative.startswith('aziende/'):
                return Response(404)
            return Response(200, (self.root / relative).read_bytes())
        self.client.open.side_effect = serve
        with self.assertRaisesRegex(CheckError, 'ancora pubblici'):
            self.check()
        self.assert_clean()
        self.assertEqual(self.client.open.call_count, 2)

    def test_redirect_fuori_origine_non_viene_accettato(self):
        self.client.open.return_value = Response(302, headers={'Location': 'https://altro.example/accounts/login/'})
        with self.assertRaisesRegex(CheckError, 'redirect'):
            self.check()
        self.assert_clean()

    def test_errori_rete_o_server_non_danno_falso_successo(self):
        for result in (URLError('offline'), Response(502), Response(200, b'pagina inattesa')):
            with self.subTest(result=type(result).__name__):
                self.client.open.side_effect = result if isinstance(result, Exception) else None
                self.client.open.return_value = result
                with self.assertRaises(CheckError):
                    self.check()
                self.assert_clean()

    def test_origine_non_https_rifiutata_prima_di_scrivere(self):
        with self.assertRaises(CheckError):
            check_tenant_media('http://gestionale.example.test', self.root)
        self.client.open.assert_not_called()
        self.assert_clean()

    def test_non_tocca_file_preesistenti(self):
        existing = self.root / 'avatars' / 'immagine.png'
        existing.parent.mkdir()
        existing.write_bytes(b'immagine originale')
        self.client.open.side_effect = [Response(403), Response(404)]
        self.check()
        self.assertEqual(existing.read_bytes(), b'immagine originale')
        self.assert_clean()

    def test_cli_restituisce_exit_code_uno_su_errore(self):
        self.client.open.return_value = Response(502)
        with redirect_stderr(StringIO()) as error:
            result = main(['https://gestionale.example.test', '--media-root', str(self.root)])
        self.assertEqual(result, 1)
        self.assertIn('nginx', error.getvalue())
        self.assert_clean()
