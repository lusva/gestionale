"""
Command: ``python manage.py sdi_verifica``

Controlla la configurazione SDI via PEC senza inviare nulla: dati
dell'anagrafica azienda, login SMTP (invio) e IMAP (ricezione), apertura
della cartella e numero di email da elaborare.

Da lanciare prima del primo invio: il canale PEC non ha un ambiente di
prova, ogni file inviato al SDI è una fattura emessa.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from stdnum.it import iva as stdnum_iva

from documenti.fatt_el import _pulisci_piva
from documenti.sdi import GIORNI_FINESTRA, PECBackend


class Command(BaseCommand):
    help = "Verifica la configurazione PEC per il SDI (nessun invio)."

    def add_arguments(self, parser):
        parser.add_argument('--giorni', type=int, default=GIORNI_FINESTRA)

    def handle(self, *args, **opts):
        from anagrafiche.models import AnagraficaAzienda
        from documenti.models import MessaggioPecElaborato

        az = AnagraficaAzienda.objects.first()
        if az is None:
            raise CommandError('Anagrafica azienda non configurata.')

        errori = 0

        def ok(testo):
            self.stdout.write(self.style.SUCCESS(f'  OK   {testo}'))

        def ko(testo):
            nonlocal errori
            errori += 1
            self.stdout.write(self.style.ERROR(f'  KO   {testo}'))

        self.stdout.write('Anagrafica azienda')
        if az.sdi_provider == AnagraficaAzienda.SdiProvider.PEC:
            ok('sdi_provider = PEC')
        else:
            ko(f'sdi_provider = {az.sdi_provider}: impostalo a "PEC SMTP/IMAP"')
        piva = _pulisci_piva(az.partita_iva)
        if not piva:
            ko('partita IVA mancante (serve per il nome file e il trasmittente)')
        elif stdnum_iva.is_valid(piva):
            ok(f'partita IVA {piva}')
        else:
            ko(f'partita IVA {az.partita_iva} non valida (11 cifre con '
               f'controllo): il SDI scarterebbe ogni fattura')
        self.stdout.write(
            f'       mittente {az.pec_mittente or "-"} → SDI '
            f'{az.pec_destinatario_sdi or "sdi01@pec.fatturapa.it"}'
        )

        try:
            backend = PECBackend(az)
        except ValueError as exc:
            ko(str(exc))
            raise CommandError(f'{errori} problemi da correggere.')

        self.stdout.write('Invio (SMTP)')
        try:
            with backend._smtp_connect():
                pass
            ok(f'login su {az.pec_smtp_host}:{az.pec_smtp_port or 465} '
               f'come {az.pec_smtp_user}')
        except Exception as exc:
            ko(f'{az.pec_smtp_host}:{az.pec_smtp_port or 465}: {exc}')

        self.stdout.write('Ricezione (IMAP)')
        if not backend._imap_configurato():
            ko('pec_imap_host/user/password mancanti: sdi_pull non riceverà nulla')
        else:
            try:
                with backend._imap_connect() as M:
                    uids = backend.cerca_recenti(M, opts['giorni'])
                ok(f'login su {az.pec_imap_host}:{az.pec_imap_port or 993}, '
                   f'cartella {az.pec_imap_folder or "INBOX"} aperta in sola lettura')
                self.stdout.write(
                    f'       {len(uids)} email negli ultimi {opts["giorni"]} giorni, '
                    f'{MessaggioPecElaborato.objects.count()} già elaborate in totale'
                )
            except Exception as exc:
                ko(f'{az.pec_imap_host}:{az.pec_imap_port or 993}: {exc}')

        if errori:
            raise CommandError(f'{errori} problemi da correggere.')
        self.stdout.write(self.style.SUCCESS('Configurazione pronta per l\'invio.'))
