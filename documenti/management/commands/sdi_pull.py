"""
Command: ``python manage.py sdi_pull``

Scarica notifiche SDI dalla casella PEC IMAP e le applica:
- aggiorna ``TestataFattura.sdi_stato`` in base al tipo (RC/NS/MC/NE/...)
- registra il messaggio in ``MessaggioSdi``
- importa eventuali fatture di acquisto ricevute (XML allegato che inizia
  con ``FatturaElettronica``) come ``TestataFatturaAcquisto`` in bozza

Argomenti:
- ``--dry-run``: stampa ma non applica
- ``--limit``: massimo numero di messaggi da processare in una run

Tipi SDI mappati:
  RC → ``ricevuta_sdi``  (ricevuta consegna SDI)
  NS → ``scartata``      (notifica scarto)
  MC → ``mancata_consegna``
  NE → ``accettata``     (notifica esito; se SUCCESS)
  DT → (no change, decorrenza termini)
  AT → ``inviata``       (attestazione trasmissione)
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from documenti.models import MessaggioSdi, TestataFattura


_STATO_FROM_TIPO = {
    'RC': TestataFattura.SdiStato.RICEVUTA_SDI,
    'NS': TestataFattura.SdiStato.SCARTATA,
    'MC': TestataFattura.SdiStato.MANCATA_CONSEGNA,
    'NE': TestataFattura.SdiStato.ACCETTATA,
    'AT': TestataFattura.SdiStato.INVIATA,
}


class Command(BaseCommand):
    help = "Scarica notifiche SDI dalla PEC e le applica alle fatture."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--limit', type=int, default=200)

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        limit = opts['limit']

        from documenti.sdi import get_backend
        backend = get_backend()
        if backend is None:
            self.stdout.write(self.style.WARNING(
                'Backend SDI non configurato (sdi_provider=disabilitato).'
            ))
            return

        msgs = list(backend.fetch_messages())[:limit]
        if not msgs:
            self.stdout.write('Nessun nuovo messaggio.')
            return

        n_notif, n_fatture, n_skip = 0, 0, 0

        for m in msgs:
            # Fattura in entrata (acquisto)?
            if m.fattura_xml:
                n = self._import_fattura_acquisto(m, dry)
                if n:
                    n_fatture += n
                continue

            # Notifica SDI: aggancio alla fattura via id_trasmissione, sdi_id_trasmissione
            # o numero/anno; in mancanza la registriamo come orfana skipped.
            fattura = self._match_fattura(m)
            if fattura is None:
                n_skip += 1
                self.stdout.write(self.style.WARNING(
                    f'  skip: {m.tipo} senza fattura associabile (id={m.id_trasmissione})'
                ))
                continue
            if dry:
                self.stdout.write(
                    f'  [DRY] {m.tipo} → fattura {fattura.numero}/{fattura.anno}: {m.descrizione[:60]}'
                )
                n_notif += 1
                continue

            MessaggioSdi.objects.create(
                fattura=fattura, tipo=m.tipo,
                id_trasmissione=m.id_trasmissione,
                descrizione=m.descrizione,
                payload=m.raw[:8000],
                direzione='in',
            )
            new_state = _STATO_FROM_TIPO.get(m.tipo)
            updates = {'sdi_ultimo_messaggio': m.descrizione[:255]}
            if m.id_trasmissione and not fattura.sdi_id_trasmissione:
                updates['sdi_id_trasmissione'] = m.id_trasmissione[:80]
            if new_state:
                updates['sdi_stato'] = new_state
            TestataFattura.objects.filter(pk=fattura.pk).update(**updates)
            n_notif += 1
            self.stdout.write(self.style.SUCCESS(
                f'  {m.tipo} → fattura {fattura.numero}/{fattura.anno}: {m.descrizione[:60]}'
            ))

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Completato: {n_notif} notifiche applicate, '
            f'{n_fatture} fatture acquisto importate, {n_skip} ignorate.'
        ))

    # === Helper ===

    def _match_fattura(self, m):
        """Trova la TestataFattura cui si riferisce una notifica SDI."""
        if m.id_trasmissione:
            f = TestataFattura.objects.filter(
                sdi_id_trasmissione=m.id_trasmissione,
            ).first()
            if f:
                return f
        # Fallback: ultima fattura inviata in attesa di notifica
        return TestataFattura.objects.filter(
            sdi_stato__in=(
                TestataFattura.SdiStato.INVIATA,
                TestataFattura.SdiStato.RICEVUTA_SDI,
            ),
        ).order_by('-sdi_data_invio').first()

    def _import_fattura_acquisto(self, m, dry):
        if dry:
            self.stdout.write(
                f'  [DRY] fattura acquisto da: {m.fattura_filename}'
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
            self.stdout.write(self.style.WARNING(
                f'  skip fattura acquisto ({m.fattura_filename}): {exc}'
            ))
            return 0
