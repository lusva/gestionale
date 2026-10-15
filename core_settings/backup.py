"""Backup completo del gestionale: il database e i file caricati.

Uno zip ``backups/gestionale_AAAAMMGG_HHMMSS.zip`` con

- ``db.sqlite3``: copia consistente fatta con l'API di backup di SQLite
  (corretta anche in modalità WAL e mentre il sito scrive: copiare a mano il
  solo file del database può perdere le ultime scritture);
- ``media/``: logo, XML delle fatture di acquisto, allegati, certificato di
  firma (le copie ridotte del logo no: si rigenerano);
- ``LEGGIMI.txt``: come ripristinarlo.

Contiene tutto, password PEC e certificato compresi: si crea e si scarica
solo da superuser, e va conservato al sicuro.
"""
from __future__ import annotations

import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from django.conf import settings

NOME_RE = re.compile(r'^gestionale_\d{8}_\d{6}\.zip$')
CONSERVA = 14
_ESCLUSI_MEDIA = ('anagrafica/ridotti/',)


def cartella() -> Path:
    return Path(settings.BASE_DIR) / 'backups'


def elenco() -> list[dict]:
    if not cartella().is_dir():
        return []
    out = []
    for p in sorted(cartella().glob('gestionale_*.zip'), reverse=True):
        if NOME_RE.match(p.name):
            st = p.stat()
            out.append({'nome': p.name, 'dimensione': st.st_size,
                        'data': datetime.fromtimestamp(st.st_mtime)})
    return out


def percorso(nome: str) -> Path | None:
    """Il file di un backup dal nome, solo se è davvero uno dei backup."""
    if not NOME_RE.match(nome or ''):
        return None
    p = cartella() / nome
    return p if p.is_file() else None


def _copia_database(destinazione: Path) -> None:
    db = settings.DATABASES['default']
    if db['ENGINE'] != 'django.db.backends.sqlite3':
        raise RuntimeError('Il backup dal gestionale funziona solo con SQLite: '
                           'per altri database usa gli strumenti del database.')
    sorgente = sqlite3.connect(str(db['NAME']), timeout=30)
    copia = sqlite3.connect(str(destinazione))
    try:
        with copia:
            sorgente.backup(copia)
    finally:
        copia.close()
        sorgente.close()


def crea(con_media: bool = True, conserva: int = CONSERVA) -> Path:
    """Crea un backup e tiene solo gli ultimi ``conserva``. Restituisce il file."""
    cartella().mkdir(parents=True, exist_ok=True)
    adesso = datetime.now()
    finale = cartella() / f'gestionale_{adesso:%Y%m%d_%H%M%S}.zip'
    parziale = finale.with_suffix('.zip.tmp')
    media_root = Path(settings.MEDIA_ROOT)
    n_file = 0
    with tempfile.TemporaryDirectory() as tmp:
        copia_db = Path(tmp) / 'db.sqlite3'
        _copia_database(copia_db)
        with zipfile.ZipFile(parziale, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(copia_db, 'db.sqlite3')
            if con_media and media_root.is_dir():
                for f in sorted(media_root.rglob('*')):
                    rel = f.relative_to(media_root).as_posix()
                    if f.is_file() and not rel.startswith(_ESCLUSI_MEDIA):
                        zf.write(f, f'media/{rel}')
                        n_file += 1
            zf.writestr('LEGGIMI.txt', '\n'.join([
                f'Backup del gestionale del {adesso:%d/%m/%Y %H:%M}.',
                f'Database SQLite e {n_file} file caricati (cartella media/).',
                '',
                'Per ripristinarlo (a sito fermo):',
                '  1. systemctl stop gestionale  (o il nome del servizio gunicorn)',
                '  2. sostituisci db.sqlite3 nella cartella del progetto con quello dello zip',
                '     e cancella eventuali db.sqlite3-wal e db.sqlite3-shm',
                '  3. copia il contenuto di media/ nella cartella media/ del progetto',
                '  4. python manage.py migrate  (se il codice è più recente del backup)',
                '  5. systemctl start gestionale',
                '',
                'Contiene dati riservati (anche le password PEC e il certificato di firma):',
                'conservalo in un posto sicuro.',
            ]) + '\n')
    parziale.replace(finale)
    for vecchio in [b['nome'] for b in elenco()][conserva:]:
        (cartella() / vecchio).unlink(missing_ok=True)
    return finale
