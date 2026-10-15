"""Copie ridotte del logo aziendale.

Il logo caricato può essere enorme (un export da programma di grafica: decine
di MB, migliaia di pixel per lato). Mostrato così com'è pesa su ogni pagina
(la sidebar lo scarica e il browser decodifica centinaia di MB di bitmap) e
finisce intero in ogni PDF. Qui se ne genera una copia ridotta per ogni uso,
una volta sola: il nome della copia dipende da dimensione e data del file
originale, quindi un nuovo caricamento produce una copia nuova.

L'originale resta intatto in ``anagrafica/``; le copie stanno in
``anagrafica/ridotti/`` e si possono cancellare in qualsiasi momento (si
rigenerano alla prima richiesta).

Nelle copie si toglie anche il margine vuoto attorno al disegno (trasparente o
di un colore chiaro uniforme): molti logo esportati hanno il marchio piccolo
al centro di una tela grande, e nell'intestazione dei documenti, dove conta
l'altezza, risulterebbe minuscolo. Un fondo crema o avorio diventa bianco.
"""
from __future__ import annotations

import functools
import hashlib
import logging
import os
import tempfile
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

# Lato massimo in pixel per ciascun uso.
LATO_SIDEBAR = 160   # mostrato a ~40 px: basta anche sugli schermi 3x
LATO_ANTEPRIMA = 400  # anteprima nella pagina dell'anagrafica azienda
LATO_PDF = 900       # ~7,5 cm a 300 dpi, più di quanto occupi l'intestazione

# Sotto queste soglie, e senza margine da togliere, l'originale va già bene e
# si usa quello.
_PESO_MAX_ORIGINALE = 300 * 1024

_CARTELLA = 'anagrafica/ridotti'

# Cambia quando cambia il modo di generare le copie: le vecchie non si usano più.
_VERSIONE = 2


def _origine(logo) -> Path | None:
    if not logo:
        return None
    try:
        percorso = Path(logo.path)
    except (NotImplementedError, ValueError):
        return None
    return percorso if percorso.is_file() else None


def _trasparente(origine: Path) -> bool:
    from PIL import Image
    with Image.open(origine) as im:
        return _con_trasparenza(im)


def _con_trasparenza(im) -> bool:
    return im.mode in ('RGBA', 'LA', 'PA') or (im.mode == 'P' and 'transparency' in im.info)


def _fondo_chiaro(rgb):
    """Il colore del fondo di un'immagine RGB (il più frequente sul bordo) se
    è chiaro; None se è scuro: potrebbe far parte del logo (scritta chiara su
    riquadro scuro) e non va toccato."""
    larghezza, altezza = rgb.size
    bordo = [rgb.getpixel((x, y))
             for x in range(0, larghezza, max(1, larghezza // 50))
             for y in (0, altezza - 1)]
    bordo += [rgb.getpixel((x, y))
              for y in range(0, altezza, max(1, altezza // 50))
              for x in (0, larghezza - 1)]
    fondo = max(set(bordo), key=bordo.count)
    return fondo if sum(fondo) / 3 >= 225 else None


def _nome_copia(origine: Path, lato: int) -> str:
    st = origine.stat()
    impronta = hashlib.sha1(
        f'{origine.name}|{st.st_size}|{st.st_mtime_ns}|{lato}|{_VERSIONE}'.encode(),
    ).hexdigest()[:10]
    # senza trasparenza il JPEG pesa un quinto del PNG (conta nei PDF)
    estensione = 'png' if _trasparente(origine) else 'jpg'
    return f'{_CARTELLA}/{origine.stem}-{lato}-{impronta}.{estensione}'


def _estremi(valori, quota=0.0025):
    """Primo e ultimo indice (escluso) che tengono il disegno, scartando ai
    due lati al massimo ``quota`` del totale. None se non c'è disegno."""
    totale = sum(valori)
    if not totale:
        return None
    limite = totale * quota
    somma, inizio = 0, 0
    for i, v in enumerate(valori):
        somma += v
        if somma > limite:
            inizio = i
            break
    somma, fine = 0, len(valori)
    for i in range(len(valori) - 1, -1, -1):
        somma += valori[i]
        if somma > limite:
            fine = i + 1
            break
    return inizio, fine


def _ritaglia_margine(im):
    """``im`` senza il margine vuoto attorno al disegno: trasparente, o di un
    colore chiaro uniforme (vedi ``_fondo_chiaro``). Puntini isolati sul
    margine (sporco dell'export) non contano."""
    from PIL import Image, ImageChops

    larghezza, altezza = im.size
    if _con_trasparenza(im):
        maschera = im.convert('RGBA').getchannel('A').point(lambda v: 255 if v > 16 else 0)
    else:
        rgb = im.convert('RGB')
        fondo = _fondo_chiaro(rgb)
        if fondo is None:
            return im
        diff = ImageChops.difference(rgb, Image.new('RGB', rgb.size, fondo))
        maschera = diff.convert('L').point(lambda v: 255 if v > 20 else 0)
    # quanto disegno c'è in ogni colonna e in ogni riga; ai lati si scartano
    # colonne e righe che insieme ne hanno una quota trascurabile
    maschera = maschera.convert('F')
    colonne = list(maschera.resize((larghezza, 1), Image.BOX).getdata())
    righe = list(maschera.resize((1, altezza), Image.BOX).getdata())
    estremi_x, estremi_y = _estremi(colonne), _estremi(righe)
    if estremi_x is None or estremi_y is None:
        return im
    (x0, x1), (y0, y1) = estremi_x, estremi_y
    aria = round(max(x1 - x0, y1 - y0) * 0.03)   # un filo d'aria attorno al disegno
    x0, y0 = max(0, x0 - aria), max(0, y0 - aria)
    x1, y1 = min(larghezza, x1 + aria), min(altezza, y1 + aria)
    if (x1 - x0) * (y1 - y0) > 0.92 * larghezza * altezza:
        return im                                # margine trascurabile
    return im.crop((x0, y0, x1, y1))


def _sbianca_fondo(im):
    """Un fondo chiaro ma non bianco (crema, avorio) portato a bianco puro:
    sulla carta e sulle pagine bianche si vedrebbe come un riquadro. Ogni
    canale si scala in proporzione, quindi i colori del disegno cambiano
    appena."""
    if _con_trasparenza(im):
        return im
    rgb = im.convert('RGB')
    fondo = _fondo_chiaro(rgb)
    if fondo is None or fondo == (255, 255, 255):
        return im
    tabella = []
    for canale in fondo:
        tabella += [min(255, round(v * 255 / canale)) for v in range(256)]
    return rgb.point(tabella)


def _genera(origine: Path, destinazione: Path, lato: int) -> None:
    from PIL import Image

    jpeg = destinazione.suffix == '.jpg'
    with Image.open(origine) as im:
        # prima si riduce a una misura comoda da analizzare, poi si ritaglia
        # il margine e si porta alla misura finale: il disegno, non la tela,
        # arriva a ``lato`` pixel
        im.thumbnail((lato * 3, lato * 3), Image.LANCZOS)
        im = _sbianca_fondo(_ritaglia_margine(im))
        im.thumbnail((lato, lato), Image.LANCZOS)
        if jpeg:
            im = im.convert('RGB')
        elif im.mode in ('P', 'LA', 'PA'):
            im = im.convert('RGBA')
        elif im.mode not in ('RGB', 'RGBA', 'L'):
            im = im.convert('RGB')
        destinazione.parent.mkdir(parents=True, exist_ok=True)
        # scrittura atomica: due worker che generano la stessa copia insieme
        # non lasciano mai un file a metà
        fd, tmp = tempfile.mkstemp(dir=destinazione.parent, suffix=destinazione.suffix)
        os.close(fd)
        try:
            if jpeg:
                im.save(tmp, 'JPEG', quality=88, optimize=True, progressive=True)
            else:
                im.save(tmp, 'PNG', optimize=True)
            # mkstemp crea il file 0600: nginx (altro utente) non lo leggerebbe
            os.chmod(tmp, settings.FILE_UPLOAD_PERMISSIONS or 0o644)
            os.replace(tmp, destinazione)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def _serve_ridurre(origine: Path, lato: int) -> bool:
    st = origine.stat()
    if st.st_size > _PESO_MAX_ORIGINALE:
        return True
    try:
        from PIL import Image
        with Image.open(origine) as im:
            if max(im.size) > lato:
                return True
        return _ha_margine(str(origine), st.st_size, st.st_mtime_ns)
    except Exception:
        return False


@functools.lru_cache(maxsize=32)
def _ha_margine(percorso: str, _peso: int, _data: int) -> bool:
    """True se il logo (già piccolo) ha un margine vuoto da togliere o un fondo
    da sbiancare. Peso e
    data del file entrano nella chiave della cache: un nuovo caricamento si
    rianalizza."""
    from PIL import Image
    with Image.open(percorso) as im:
        im.load()
        if _ritaglia_margine(im).size != im.size:
            return True
        return not _con_trasparenza(im) and _fondo_chiaro(im.convert('RGB')) not in (None, (255, 255, 255))


def logo_ridotto_nome(logo, lato: int) -> str | None:
    """Nome (relativo a MEDIA_ROOT) della copia del logo con lato massimo
    ``lato``, generata se manca. L'originale se è già abbastanza piccolo o se
    la riduzione non riesce; None senza logo."""
    origine = _origine(logo)
    if origine is None:
        return logo.name if logo else None
    try:
        if not _serve_ridurre(origine, lato):
            return logo.name
        nome = _nome_copia(origine, lato)
        destinazione = Path(settings.MEDIA_ROOT) / nome
        if not destinazione.is_file():
            _genera(origine, destinazione, lato)
        return nome
    except Exception:
        logger.exception('Riduzione del logo %s non riuscita', origine)
        return logo.name


def logo_ridotto_url(logo, lato: int) -> str:
    """URL della copia ridotta del logo ('' senza logo)."""
    if not logo:
        return ''
    nome = logo_ridotto_nome(logo, lato)
    if not nome:
        return ''
    if nome == logo.name:
        return logo.url
    return logo.storage.url(nome)
