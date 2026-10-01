"""File caricati che non devono essere pubblici.

``/media/<cartella>/…`` per le cartelle di ``PROTETTI`` passa da Django:
serve il login e il permesso del ruolo. In produzione Django risponde solo
con ``X-Accel-Redirect`` e il file lo spedisce nginx da una location
``internal`` (``settings.MEDIA_PROTETTI_ACCEL``); in sviluppo ``FileResponse``.
Le altre cartelle di media (logo, avatar) restano pubbliche.
"""
import mimetypes
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse

from accounts.permissions import has_perm

# cartella (upload_to) → permesso che serve per scaricare. None = mai via web
# (il .p12 della firma digitale contiene la chiave privata).
PROTETTI = {
    'certificati': None,
    'rimborsi_km': 'cashflow.vedi',
    'fatture_acquisto_xml': 'documenti.vedi',
}
CARTELLE_REGEX = '|'.join(PROTETTI)


@login_required
def media_protetto(request, cartella, percorso):
    if cartella not in PROTETTI:
        raise Http404
    perm = PROTETTI[cartella]
    if perm is None:
        raise Http404
    if not has_perm(request.user, perm):
        raise PermissionDenied
    base = (Path(settings.MEDIA_ROOT) / cartella).resolve()
    file = (base / percorso).resolve()
    if base not in file.parents or not file.is_file():
        raise Http404
    relativo = file.relative_to(Path(settings.MEDIA_ROOT).resolve()).as_posix()
    accel = getattr(settings, 'MEDIA_PROTETTI_ACCEL', '')
    if accel:
        risposta = HttpResponse()
        risposta['X-Accel-Redirect'] = accel.rstrip('/') + '/' + quote(relativo)
        risposta['Content-Type'] = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
    else:
        risposta = FileResponse(file.open('rb'))
    risposta['Content-Disposition'] = f"inline; filename*=UTF-8''{quote(file.name)}"
    risposta['X-Content-Type-Options'] = 'nosniff'
    risposta['Cache-Control'] = 'private, no-store'
    return risposta
