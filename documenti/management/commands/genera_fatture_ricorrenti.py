"""
Command: ``python manage.py genera_fatture_ricorrenti``

Emette le fatture ricorrenti dei periodi arrivati a scadenza (anche quelli
saltati: le fatture sono comunque datate oggi, con il periodo nella
descrizione) e, se la ricorrenza lo prevede, le invia al SDI e per email.
Lo lancia ogni mattina il timer ``deploy/gestionale-ricorrenti.timer``.

Idempotente: ogni periodo si emette una volta sola (``EmissioneRicorrente``),
lanciarlo due volte nello stesso giorno non crea doppioni. Un errore su una
ricorrenza resta registrato e non ferma le altre.

Argomenti:
- ``--dry-run``: elenca cosa emetterebbe, senza scrivere né inviare
- ``--data AAAA-MM-GG``: il giorno da considerare «oggi» (prove, recuperi)
"""
from __future__ import annotations

from datetime import date

from django.core.management.base import CommandError
from tenancy.commands import AziendeCommand
from django.utils.timezone import localdate

from documenti import ricorrenti
from documenti.models import EmissioneRicorrente, FatturaRicorrente


class Command(AziendeCommand):
    help = 'Emette le fatture ricorrenti dei periodi arrivati a scadenza.'

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--data', help='giorno di emissione (AAAA-MM-GG), di norma oggi')

    def handle_azienda(self, *args, dry_run=False, data=None, **opts):
        try:
            oggi = date.fromisoformat(data) if data else localdate()
        except ValueError:
            raise CommandError('Data non valida: usa AAAA-MM-GG.')
        if dry_run:
            self._elenca(oggi)
            return
        from core_settings.esecuzioni import GiaInCorso, esecuzione
        try:
            with esecuzione('genera_fatture_ricorrenti') as stato:
                emissioni = ricorrenti.genera(oggi)
                ok = [e for e in emissioni if e.esito == EmissioneRicorrente.Esito.EMESSA]
                ko = [e for e in emissioni if e.esito == EmissioneRicorrente.Esito.ERRORE]
                for e in ok:
                    self.stdout.write(self.style.SUCCESS(
                        f'  {e.ricorrenza}: fattura {e.fattura.numero}/{e.fattura.anno} '
                        f'({ricorrenti.periodo(e.ricorrenza, e.periodo).etichetta})'))
                    for riga in filter(None, e.invio.splitlines()):
                        self.stdout.write(f'      {riga}')
                for e in ko:
                    self.stdout.write(self.style.ERROR(f'  {e.ricorrenza}: {e.errore}'))
                stato.riepilogo = f'{len(ok)} fatture emesse, {len(ko)} non riuscite.'
                if ko:
                    stato.esito = 'errore'
                self.stdout.write(stato.riepilogo)
        except GiaInCorso:
            self.stdout.write(self.style.WARNING('Emissione già in corso: questa run salta.'))

    def _elenca(self, oggi):
        n = 0
        for r in FatturaRicorrente.objects.filter(attiva=True).select_related('cliente').order_by('pk'):
            for p in ricorrenti.da_emettere(r, oggi):
                n += 1
                self.stdout.write(f'  [DRY] {r} — {r.cliente}: periodo {p.etichetta} '
                                  f'(previsto il {p.previsto:%d/%m/%Y}), fattura del {oggi:%d/%m/%Y}')
        self.stdout.write(f'Dry-run (nulla di scritto): {n} fatture da emettere.')
