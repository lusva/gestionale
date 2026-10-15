"""Storage locale: ogni nuovo upload ha un percorso proprio dell'azienda."""
from pathlib import PurePosixPath

from django.core.exceptions import PermissionDenied, SuspiciousFileOperation
from django.core.files.storage import FileSystemStorage

from .context import current_azienda_id


class TenantFileSystemStorage(FileSystemStorage):
    def _tenant_name(self, name):
        azienda_id = current_azienda_id()
        if azienda_id is None:
            raise PermissionDenied('È necessaria un’azienda per caricare file.')
        name = str(name).replace('\\', '/')
        parts = PurePosixPath(name).parts
        if not parts or name.startswith('/') or '..' in parts:
            raise SuspiciousFileOperation('Percorso del file non valido.')
        prefix = f'aziende/{azienda_id}/'
        if parts[0] == 'aziende':
            if not name.startswith(prefix):
                raise PermissionDenied('Il file appartiene a un’altra azienda.')
            return name
        return prefix + name

    def get_available_name(self, name, max_length=None):
        # Il prefisso partecipa al limite di lunghezza del FileField e al
        # controllo dei duplicati, anche per default_storage.save().
        return super().get_available_name(self._tenant_name(name), max_length=max_length)

    def _save(self, name, content):
        return super()._save(self._tenant_name(name), content)
