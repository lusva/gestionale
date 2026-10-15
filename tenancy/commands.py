"""Comandi operativi eseguiti separatamente per ciascuna azienda attiva."""
from django.core.management.base import BaseCommand, CommandError

from .context import azienda_context
from .models import Azienda


class AziendeCommand(BaseCommand):
    """Senza argomenti mantiene i timer esistenti, includendo tutte le aziende.

    I comandi richiamati da una richiesta web devono sempre passare ``azienda``.
    Una singola azienda non configurata non impedisce l'esecuzione delle altre.
    """

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument('--azienda', type=int,
                            help='ID azienda; se omesso esegue per tutte le aziende attive.')

    def handle(self, *args, **options):
        azienda_id = options.pop('azienda', None)
        aziende = Azienda.objects.filter(attiva=True).order_by('pk')
        if azienda_id is not None:
            aziende = aziende.filter(pk=azienda_id)
        aziende = list(aziende)
        if azienda_id is not None and not aziende:
            raise CommandError('Azienda inesistente o disattivata.')
        errori = []
        for azienda in aziende:
            self.stdout.write(f'Azienda {azienda.pk}: {azienda.nome}')
            try:
                with azienda_context(azienda.pk):
                    self.handle_azienda(*args, **options)
            except Exception as exc:
                if azienda_id is not None:
                    raise
                errori.append(str(azienda.pk))
                self.stderr.write(self.style.ERROR(f'Azienda {azienda.pk}: {exc}'))
        if errori:
            raise CommandError('Esecuzione non riuscita per le aziende: ' + ', '.join(errori))

    def handle_azienda(self, *args, **options):
        raise NotImplementedError
