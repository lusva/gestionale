"""Tutti i media richiedono login, azienda proprietaria e permesso del ruolo.

La proprietà è verificata sui record, anche per i file senza prefisso creati
prima della separazione per azienda. I certificati non sono mai scaricabili.
Nginx può spedire il file solo dopo l'autorizzazione, via X-Accel-Redirect.
"""
import mimetypes
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from django.apps import apps
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse

from accounts.permissions import has_perm
from tenancy.context import current_azienda_id


# cartella → (modello, campo, permesso). I certificati sono esclusi.
MEDIA_FIELDS = {
    'rimborsi_km': ('cashflow.RimborsoChilometrico', 'allegato', 'cashflow.vedi'),
    'fatture_acquisto_xml': ('documenti.TestataFatturaAcquisto', 'xml_originale', 'documenti.vedi'),
    'fatture_acquisto_allegati': ('documenti.TestataFatturaAcquisto', 'allegato', 'documenti.vedi'),
    'anagrafica': ('anagrafiche.AnagraficaAzienda', 'logo', ''),
    'avatars': ('accounts.Profile', 'avatar', ''),
}


def _cartella(nome, azienda_id):
    """Rifiuta percorsi ambigui e prefissi di altre aziende."""
    if not nome or '\\' in nome or nome.startswith('/'):
        return None
    parts = PurePosixPath(nome).parts
    if '..' in parts or '/'.join(parts) != nome:
        return None
    if parts[0] == 'aziende':
        if len(parts) < 4 or parts[1] != str(azienda_id):
            return None
        parts = parts[2:]
    return parts[0] if len(parts) >= 2 else None


def file_appartiene_azienda(nome, azienda_id):
    """True soltanto per file referenziati dall'azienda o copie del suo logo."""
    if azienda_id is None:
        return False
    cartella = _cartella(nome, azienda_id)
    spec = MEDIA_FIELDS.get(cartella)
    if spec is None:
        return False
    model_label, field, _perm = spec
    model = apps.get_model(model_label)
    # Il filtro è esplicito anche quando il manager è già limitato dal contesto.
    records = model._default_manager.filter(azienda_id=azienda_id)
    if records.filter(**{field: nome}).exists():
        return True
    if cartella != 'anagrafica':
        return False
    from anagrafiche.logo import (
        LATO_ANTEPRIMA, LATO_PDF, LATO_SIDEBAR, _nome_copia, _origine,
    )
    for record in records.exclude(logo='').exclude(logo__isnull=True):
        origine = _origine(record.logo)
        if origine is None:
            continue
        for lato in (LATO_SIDEBAR, LATO_ANTEPRIMA, LATO_PDF):
            try:
                if nome == _nome_copia(origine, lato, azienda_id=azienda_id):
                    return True
            except (OSError, ValueError):
                continue
    return False


@login_required
def media_protetto(request, percorso):
    azienda_id = current_azienda_id()
    if not file_appartiene_azienda(percorso, azienda_id):
        raise Http404
    perm = MEDIA_FIELDS[_cartella(percorso, azienda_id)][2]
    if perm and not has_perm(request.user, perm):
        raise PermissionDenied
    base = Path(settings.MEDIA_ROOT).resolve()
    file = (base / percorso).resolve()
    if base not in file.parents or not file.is_file():
        raise Http404
    relativo = file.relative_to(base).as_posix()
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
