"""
Command: ``python manage.py allinea_registrazione_acquisti``

Porta la data di registrazione delle fatture di acquisto importate da XML
alla data del documento. Fino al fix in ``xml_import`` l'import la lasciava
al default (il giorno dell'import), così la liquidazione IVA, che conta gli
acquisti alla data di registrazione, metteva tutta l'IVA a credito delle
fatture importate in ritardo nel periodo dell'import.

Tocca solo le fatture con l'XML originale e una data di registrazione
diversa da quella del documento.

Argomenti:
- ``--dry-run``: elenca le fatture ma non le modifica
"""
from tenancy.commands import AziendeCommand
from django.db.models import F

from documenti.models import TestataFatturaAcquisto


class Command(AziendeCommand):
    help = ('Allinea la data di registrazione delle fatture di acquisto '
            'importate da XML alla data del documento.')

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument('--dry-run', action='store_true',
                            help='Elenca le fatture senza modificarle.')

    def handle_azienda(self, *args, dry_run=False, **opts):
        qs = (TestataFatturaAcquisto.objects
              .exclude(xml_originale='').exclude(xml_originale__isnull=True)
              .exclude(data_documento__isnull=True)
              .exclude(data_registrazione=F('data_documento'))
              .select_related('fornitore').order_by('data_documento'))
        fatture = list(qs)
        for f in fatture:
            reg = f.data_registrazione.strftime('%d/%m/%Y') if f.data_registrazione else '—'
            self.stdout.write(
                f'  {f.numero}/{f.anno} {f.fornitore or ""} n. {f.numero_fornitore or "—"}: '
                f'registrazione {reg} → {f.data_documento:%d/%m/%Y}'
            )
        if dry_run:
            self.stdout.write(f'[DRY] {len(fatture)} fatture da allineare.')
            return
        # update() e non save(): il post_save della testata ricalcolerebbe
        # imponibile e imposta dalle righe, perdendo gli importi dell'XML
        n = qs.update(data_registrazione=F('data_documento'))
        self.stdout.write(self.style.SUCCESS(f'{n} fatture allineate.'))
