"""
Command: ``python manage.py sdi_pull``

Scarica notifiche SDI dalla casella PEC IMAP e le applica:
- aggiorna ``TestataFattura.sdi_stato`` in base al tipo (RC/NS/MC/NE/...)
- registra il messaggio in ``MessaggioSdi``
- importa eventuali fatture di acquisto ricevute (XML o ``.xml.p7m``
  firmato, con radice ``FatturaElettronica``) come
  ``TestataFatturaAcquisto`` in bozza
- salva sull'anagrafica la casella ``sdiNN@pec.fatturapa.it`` da cui il SDI
  risponde al primo invio: è quella da usare per gli invii successivi

Si considerano solo le email spedite dal SDI; le altre email della PEC
aziendale vengono saltate (e segnalate se hanno allegati con nome da SDI).

Argomenti:
- ``--dry-run``: stampa ma non applica
- ``--limit``: massimo numero di email da processare in una run
- ``--giorni``: cerca le email arrivate negli ultimi N giorni (default 30);
  per scaricare le fatture passive più vecchie, es. ``--giorni 365``

Tipi SDI mappati:
  RC → ``accettata``     (ricevuta di consegna: per B2B/B2C è lo stato
       finale, la NE esiste solo verso la PA)
  NS → ``scartata``      (notifica scarto)
  MC → ``mancata_consegna``
  NE → dipende dall'Esito: EC01 → ``accettata``, EC02 (rifiuto del
       committente) → ``rifiutata``; esito assente/ignoto → nessun cambio
  DT → (no change, decorrenza termini)
  AT → ``inviata``       (attestazione trasmissione)

Aggancio notifica → fattura: per IdentificativoSdI (se già noto) oppure per
il nome del file inviato (NomeFile della notifica, costruito all'invio con
``fatt_el.nome_file_sdi``). Una notifica non riconducibile con certezza viene
ignorata e loggata: mai assegnata "all'ultima fattura inviata".

Affidabilità: i flag della casella (letto/non letto) non vengono mai
toccati. Un'email viene registrata come elaborata (``MessaggioPecElaborato``)
solo dopo che tutto il suo contenuto è stato elaborato con successo; un
errore su un'email non interrompe il batch (l'email resta da elaborare e
viene ripresa alla run successiva, finché è nella finestra di ``--giorni``).
``--dry-run`` non registra nulla.
"""
from __future__ import annotations

import logging
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from documenti.models import MessaggioSdi, TestataFattura, TestataNotaCredito
from documenti.sdi import CASELLA_SDI_PRIMO_INVIO, GIORNI_FINESTRA

logger = logging.getLogger(__name__)


_STATO_FROM_TIPO = {
    # RC = consegnata al destinatario. Per B2B/B2C non arriva altro; per la PA
    # può seguire una NE che conferma (EC01) o rifiuta (EC02).
    'RC': TestataFattura.SdiStato.ACCETTATA,
    'NS': TestataFattura.SdiStato.SCARTATA,
    'MC': TestataFattura.SdiStato.MANCATA_CONSEGNA,
    'AT': TestataFattura.SdiStato.INVIATA,
}

# Notifica Esito (NE): lo stato dipende dall'esito del committente.
_STATO_FROM_ESITO_NE = {
    'EC01': TestataFattura.SdiStato.ACCETTATA,
    'EC02': TestataFattura.SdiStato.RIFIUTATA,
}

# IT<idcodice>_<progressivo>[_<TIPO>_<nnn>][.xml][.p7m]
_RE_NOME_FILE = re.compile(
    r'^(?P<paese>[A-Z]{2})(?P<codice>[A-Z0-9]+)_(?P<progr>[A-Z0-9]{1,10})'
    r'(?:_[A-Z]{2}_[A-Z0-9]+)?(?:\.XML)?(?:\.P7M)?$',
    re.I,
)


def stato_da_notifica(m):
    """Nuovo ``sdi_stato`` per una notifica SDI (``None`` = nessun cambio)."""
    if m.tipo == 'NE':
        return _STATO_FROM_ESITO_NE.get((m.esito or '').strip().upper())
    return _STATO_FROM_TIPO.get(m.tipo)


def progressivo_da_nome_file(nome: str) -> str:
    """Estrae il progressivo (maiuscolo) da un nome file SDI, '' se non valido."""
    nome = (nome or '').strip().rsplit('/', 1)[-1]
    match = _RE_NOME_FILE.match(nome)
    return match.group('progr').upper() if match else ''


def match_documento(m):
    """Trova la fattura o la nota di credito cui si riferisce una notifica SDI.

    1. ``IdentificativoSdI`` già registrato sul documento;
    2. progressivo del file inviato (NomeFile della notifica o, in
       mancanza, il prefisso del nome dell'allegato notifica), confrontato
       con ``progressivo_sdi`` dei documenti effettivamente inviati: con la
       ``N`` in testa è una nota di credito, altrimenti una fattura.

    Nessun fallback euristico: se non c'è corrispondenza univoca → ``None``.
    """
    from documenti.fatt_el import PREFISSO_NOTA_CREDITO, progressivo_sdi

    if m.id_trasmissione:
        for modello in (TestataFattura, TestataNotaCredito):
            qs = modello.objects.filter(sdi_id_trasmissione=m.id_trasmissione)
            if qs.count() == 1:
                return qs.first()

    progr = progressivo_da_nome_file(m.nome_file) or progressivo_da_nome_file(m.filename)
    if not progr:
        return None
    modello, cifre = TestataFattura, progr
    if progr.startswith(PREFISSO_NOTA_CREDITO):
        modello, cifre = TestataNotaCredito, progr[1:]
    inviati = modello.objects.exclude(sdi_stato=TestataFattura.SdiStato.NON_INVIATA)
    # Formato legacy delle fatture (prima di ``progressivo_sdi``):
    # <numero:05d><anno>. Accettato solo se identifica un'unica fattura
    # inviata (i sezionali possono ripetere numero/anno).
    if modello is TestataFattura and len(progr) == 9 and progr.isdigit():
        qs = inviati.filter(numero=int(progr[:5]), anno=int(progr[5:]))
        if qs.count() == 1:
            return qs.first()
        return None
    # Progressivo = (pk * 36 + tentativo) in base 36 (vedi
    # ``fatt_el.progressivo_sdi``). Una notifica di un invio precedente
    # (tentativo diverso dall'attuale) non corrisponde e viene ignorata.
    try:
        pk, _tentativo = divmod(int(cifre, 36), 36)
    except ValueError:
        return None
    documento = inviati.filter(pk=pk).first()
    if documento is not None and progressivo_sdi(documento) == progr:
        return documento
    return None


# nome storico, usato da chi importava la funzione
match_fattura = match_documento


class Command(BaseCommand):
    help = "Scarica notifiche SDI dalla PEC e le applica alle fatture."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--limit', type=int, default=200)
        parser.add_argument('--giorni', type=int, default=GIORNI_FINESTRA)

    def handle(self, *args, **opts):
        if opts['dry_run']:
            self._esegui(opts)
            return
        # Ogni run si registra (Impostazioni › Integrazioni mostra se il timer
        # gira) e non parte se ce n'è già una in corso: il timer e il pulsante
        # «Controlla ora la PEC» elaborerebbero due volte le stesse email.
        from core_settings.esecuzioni import GiaInCorso, esecuzione
        try:
            with esecuzione('sdi_pull') as stato:
                self._esegui(opts, stato)
        except GiaInCorso:
            self.stdout.write(self.style.WARNING(
                'Un controllo della PEC è già in corso: questa run salta.'
            ))

    def _esegui(self, opts, stato=None):
        dry = opts['dry_run']
        limit = opts['limit']

        from documenti.sdi import get_backend
        try:
            backend = get_backend()
        except ValueError as exc:  # configurazione PEC incompleta
            raise CommandError(str(exc))
        if backend is None:
            self.stdout.write(self.style.WARNING(
                'Backend SDI non configurato (sdi_provider=disabilitato).'
            ))
            if stato:
                stato.riepilogo = 'Invio al SDI disabilitato: nulla da controllare.'
            return

        # Il backend non registra nulla come elaborato: lo facciamo noi dopo
        # l'elaborazione. ``limit`` è applicato dal backend sui messaggi
        # sorgente: gli eccedenti restano da elaborare per la run successiva.
        msgs = list(backend.fetch_messages(limit=limit, giorni=opts['giorni']))
        errore_casella = getattr(backend, 'ultimo_errore', '')
        if errore_casella:
            self.stdout.write(self.style.ERROR(f'  {errore_casella}'))
            if stato:
                stato.esito, stato.riepilogo = 'errore', errore_casella
        if not msgs:
            self.stdout.write('Nessun nuovo messaggio.')
            if stato and not errore_casella:
                stato.riepilogo = 'Nessun nuovo messaggio.'
            return

        n_notif, n_fatture, n_skip, n_err = 0, 0, 0, 0
        # uid → True se tutti i messaggi di quel sorgente sono andati a buon fine
        esito_uid: dict[str, bool] = {}

        for m in msgs:
            try:
                with transaction.atomic():
                    esito = self._processa(m, dry)
            except Exception as exc:
                logger.exception('sdi_pull: errore elaborando %s (uid=%s)', m.tipo, m.uid)
                self.stdout.write(self.style.ERROR(
                    f'  errore su {m.tipo} (uid={m.uid}): {exc}'
                ))
                n_err += 1
                esito_uid[m.uid] = False
                continue
            esito_uid.setdefault(m.uid, True)
            if esito == 'notifica':
                n_notif += 1
            elif esito == 'fattura':
                n_fatture += 1
            elif esito == 'skip':
                n_skip += 1

        if not dry:
            da_marcare = [uid for uid, ok in esito_uid.items() if ok and uid]
            if da_marcare:
                backend.mark_processed(da_marcare)

        self.stdout.write('')
        notif, fatt = ('da applicare', 'da importare') if dry else ('applicate', 'importate')
        riepilogo = (
            f'{n_notif} notifiche {notif}, '
            f'{n_fatture} fatture acquisto {fatt}, {n_skip} ignorate, '
            f'{n_err} in errore (restano da elaborare).'
        )
        self.stdout.write(self.style.SUCCESS(
            f'{"Dry-run (nulla di scritto)" if dry else "Completato"}: {riepilogo}'
        ))
        if stato:
            if n_err or errore_casella:
                stato.esito = 'errore'
            stato.riepilogo = ' · '.join(filter(None, [errore_casella, riepilogo]))

    # === Elaborazione singolo messaggio ===

    def _processa(self, m, dry):
        """Elabora un messaggio. Ritorna 'notifica' | 'fattura' | 'skip' | ''.

        Solleva eccezione in caso di errore (il chiamante non marca letto).
        """
        if m.tipo == 'vuoto':
            return ''
        if m.tipo == 'ignorato':
            self.stdout.write(self.style.WARNING(f'  skip: {m.descrizione}'))
            return 'skip'

        # Fattura in entrata (acquisto)?
        if m.fattura_xml:
            return 'fattura' if self._import_fattura_acquisto(m, dry) else 'skip'

        self._aggiorna_casella_sdi(m, dry)
        fattura = self._match_fattura(m)          # fattura o nota di credito
        if fattura is None:
            logger.warning(
                'sdi_pull: notifica %s non associabile (id=%s, NomeFile=%s, file=%s)',
                m.tipo, m.id_trasmissione, m.nome_file, m.filename,
            )
            self.stdout.write(self.style.WARNING(
                f'  skip: {m.tipo} senza fattura associabile '
                f'(id={m.id_trasmissione or "-"}, file={m.nome_file or m.filename or "-"})'
            ))
            return 'skip'

        new_state = stato_da_notifica(m)
        if dry:
            self.stdout.write(
                f'  [DRY] {m.tipo} → {fattura}: '
                f'{m.descrizione[:60]}'
                + (f' → stato {new_state}' if new_state else '')
            )
            return 'notifica'

        nota = isinstance(fattura, TestataNotaCredito)
        MessaggioSdi.objects.create(
            **{'nota_credito' if nota else 'fattura': fattura}, tipo=m.tipo,
            id_trasmissione=m.id_trasmissione,
            descrizione=m.descrizione[:255],
            payload=m.raw[:8000],
            direzione='in',
        )
        updates = {'sdi_ultimo_messaggio': m.descrizione[:255]}
        if m.id_trasmissione and not fattura.sdi_id_trasmissione:
            updates['sdi_id_trasmissione'] = m.id_trasmissione[:80]
        # una RC elaborata in ritardo non deve annullare un rifiuto (NE EC02)
        if new_state and not (
            m.tipo == 'RC'
            and fattura.sdi_stato == TestataFattura.SdiStato.RIFIUTATA
        ):
            updates['sdi_stato'] = new_state
        type(fattura).objects.filter(pk=fattura.pk).update(**updates)
        self.stdout.write(self.style.SUCCESS(
            f'  {m.tipo} → {fattura}: {m.descrizione[:60]}'
        ))
        return 'notifica'

    # === Helper ===

    def _match_fattura(self, m):
        """Trova la fattura o nota di credito cui si riferisce una notifica SDI."""
        return match_documento(m)

    def _aggiorna_casella_sdi(self, m, dry):
        """Il SDI risponde al primo invio da una sua casella sdiNN, da usare
        per tutti gli invii successivi: la si salva sull'anagrafica finché
        questa ha ancora la casella del primo invio (o nessuna)."""
        if not m.mittente:
            return
        from anagrafiche.models import AnagraficaAzienda
        az = AnagraficaAzienda.objects.first()
        if az is None:
            return
        attuale = (az.pec_destinatario_sdi or '').strip().lower()
        if attuale not in ('', CASELLA_SDI_PRIMO_INVIO) or m.mittente == attuale:
            return
        if dry:
            self.stdout.write(
                f'  [DRY] casella SDI per i prossimi invii → {m.mittente}'
            )
            return
        AnagraficaAzienda.objects.filter(pk=az.pk).update(
            pec_destinatario_sdi=m.mittente,
        )
        self.stdout.write(self.style.SUCCESS(
            f'  casella SDI per i prossimi invii → {m.mittente}'
        ))

    def _import_fattura_acquisto(self, m, dry):
        if dry:
            # Legge l'XML come l'import, senza scrivere nulla.
            from documenti.xml_import import parse_fatture_xml
            try:
                lotto = parse_fatture_xml(m.fattura_xml)
            except ValueError as exc:
                self.stdout.write(self.style.WARNING(
                    f'  [DRY] fattura acquisto non importabile '
                    f'({m.fattura_filename}): {exc}'
                ))
                return 0
            for parsed in lotto:
                ced, t = parsed['cedente'], parsed['testata']
                fornitore = (ced['denominazione']
                             or f"{ced['nome']} {ced['cognome']}".strip())
                data = t['data'].strftime('%d/%m/%Y') if t['data'] else '-'
                self.stdout.write(
                    f'  [DRY] fattura acquisto {fornitore}: '
                    f'{t["tipo_documento"]} n. {t["numero"]} del {data}, '
                    f'totale {t["totale_documento"]} € ({m.fattura_filename})'
                )
            return 1
        from documenti.xml_import import import_fattura_from_xml
        try:
            result = import_fattura_from_xml(
                m.fattura_xml, filename=m.fattura_filename,
            )
            self.stdout.write(self.style.SUCCESS(
                f'  fattura acquisto importata: '
                f'#{result["numero_protocollo"]}/{result["anno_protocollo"]}'
                + (' [nuovo fornitore]' if result['fornitore_creato'] else '')
            ))
            return 1
        except ValueError as exc:
            # Dato non importabile (es. già importata): definitivo, il
            # messaggio si considera elaborato.
            self.stdout.write(self.style.WARNING(
                f'  skip fattura acquisto ({m.fattura_filename}): {exc}'
            ))
            return 0
