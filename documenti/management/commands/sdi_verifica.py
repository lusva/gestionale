"""
Command: ``python manage.py sdi_verifica``

Controlla la configurazione SDI via PEC senza inviare nulla: dati
dell'anagrafica azienda, login SMTP (invio) e IMAP (ricezione), apertura
della cartella e numero di email da elaborare. Gli stessi controlli del
pulsante «Verifica connessione» in Impostazioni › Integrazioni
(``documenti.sdi.verifica_configurazione``).

Da lanciare prima del primo invio: il canale PEC non ha un ambiente di
prova, ogni file inviato al SDI è una fattura emessa.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from documenti.sdi import GIORNI_FINESTRA, verifica_configurazione


class Command(BaseCommand):
    help = "Verifica la configurazione PEC per il SDI (nessun invio)."

    def add_arguments(self, parser):
        parser.add_argument('--giorni', type=int, default=GIORNI_FINESTRA)
        parser.add_argument('--timeout', type=int, default=30,
                            help='secondi di attesa per ciascun server')

    def handle(self, *args, **opts):
        from anagrafiche.models import AnagraficaAzienda

        esiti = verifica_configurazione(
            AnagraficaAzienda.objects.first(),
            giorni=opts['giorni'], timeout=opts['timeout'],
        )
        sezione = None
        for e in esiti:
            if e.sezione != sezione:
                sezione = e.sezione
                self.stdout.write(sezione)
            if e.ok is None:
                self.stdout.write(f'       {e.testo}')
            elif e.ok:
                self.stdout.write(self.style.SUCCESS(f'  OK   {e.testo}'))
            else:
                self.stdout.write(self.style.ERROR(f'  KO   {e.testo}'))

        errori = sum(1 for e in esiti if e.ok is False)
        if errori:
            raise CommandError(f'{errori} problemi da correggere.')
        self.stdout.write(self.style.SUCCESS('Configurazione pronta per l\'invio.'))
