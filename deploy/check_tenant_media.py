#!/usr/bin/env python3
"""Blocca il deploy se nginx espone ancora i media senza autenticazione.

Scrive due file temporanei con contenuto sintetico, verifica HTTPS senza
credenziali e senza seguire redirect, quindi li elimina sempre. Nessun dato
aziendale viene letto o inviato. Va eseguito sul server prima delle migrazioni.
"""
from __future__ import annotations

from pathlib import Path
import secrets
import ssl
import sys
from urllib.parse import urljoin
from urllib.request import HTTPSHandler, Request, build_opener

if __package__:
    from .check_https import (
        CheckError, NoRedirect, SafeArgumentParser, https_origin,
        open_response, same_https_url,
    )
else:
    from check_https import (
        CheckError, NoRedirect, SafeArgumentParser, https_origin,
        open_response, same_https_url,
    )


REMEDY = (
    'Installa la configurazione aggiornata deploy/nginx-gestionale.conf '
    '(tutti i /media/ devono passare da Django), verifica con nginx -t e '
    'ricarica nginx prima di ripetere il deploy.'
)


def check_tenant_media(origin, media_root=None):
    origin = https_origin(origin)
    root = Path(media_root) if media_root is not None else Path(__file__).resolve().parent.parent / 'media'
    nonce = secrets.token_hex(20)
    payload = f'tenant-media-probe:{nonce}'.encode('ascii')
    names = (
        f'aziende/1/tenant-check-{nonce}.txt',
        f'avatars/tenant-check-{nonce}.txt',
    )
    # HTTPSHandler mantiene la validazione standard del certificato. Non
    # impostare cookie: il controllo deve rappresentare un visitatore anonimo.
    client = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    created = []
    created_dirs = []
    try:
        for name in names:
            target = root / name
            missing = []
            parent = target.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for parent in reversed(missing):
                try:
                    parent.mkdir()
                except FileExistsError:
                    continue
                created_dirs.append(parent)
                parent.chmod(0o755)
            with target.open('xb') as output:
                created.append(target)
                output.write(payload)
            # Accessibile anche quando il deploy ha una umask restrittiva:
            # un 403 per permessi filesystem non deve mascherare nginx errato.
            target.chmod(0o644)
        for name in names:
            url = origin + '/media/' + name
            request = Request(url, headers={
                'User-Agent': 'GestionaleCRM-Tenant-Media-Check/1.0',
                'Cache-Control': 'no-cache, no-store',
            })
            with open_response(client, request, 'Protezione media') as response:
                if response.status in {401, 403, 404}:
                    continue
                if response.status in {301, 302, 303, 307, 308}:
                    destination = urljoin(url, response.headers.get('Location', ''))
                    if same_https_url(destination, origin + '/accounts/login/', compare_query=False):
                        continue
                    raise CheckError('Media: redirect diverso dal login HTTPS atteso. ' + REMEDY)
                if response.status == 200 and response.read(len(payload) + 1) == payload:
                    raise CheckError('Media aziendali ancora pubblici: deploy interrotto. ' + REMEDY)
                raise CheckError(
                    f'Media: impossibile confermare la protezione (HTTP {response.status}). ' + REMEDY
                )
    finally:
        for target in created:
            target.unlink(missing_ok=True)
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                # Un worker potrebbe aver creato un vero file nel frattempo.
                # Rimuovere solo directory ancora vuote.
                pass
    print('OK: file aziendali e avatar non accessibili anonimamente; file di controllo rimossi.')


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument('origin', metavar='ORIGINE_HTTPS')
    parser.add_argument('--media-root', type=Path, help='directory media locale (default: media/ del progetto)')
    try:
        args = parser.parse_args(argv)
        check_tenant_media(args.origin, args.media_root)
    except CheckError as error:
        print(f'ERRORE: {error}', file=sys.stderr)
        return 1
    except Exception as error:
        print(f'ERRORE: controllo media non completato ({type(error).__name__}).', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
