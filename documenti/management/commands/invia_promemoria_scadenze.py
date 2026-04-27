"""
Command: ``python manage.py invia_promemoria_scadenze``

Invia email di promemoria per le scadenze attive dei clienti — sia per
fatture non pagate che stanno per scadere, sia per scaduti recenti.

Argomenti:
- ``--giorni`` (default 7): considera scadenze nei prossimi N giorni e
  scaduti negli ultimi N giorni.
- ``--dry-run``: calcola e stampa quante email invierebbe ma non invia.
- ``--admin``: invia anche un riepilogo all'admin (settings.DEFAULT_FROM_EMAIL).

Logica destinatario:
1. Cliente.pec → preferito
2. Email del contatto principale → fallback
3. Salta se nessuno disponibile

Per cliente raggruppa tutte le scadenze in una sola email.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.mail import EmailMessage, send_mail
from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils.timezone import now

from documenti.models import ScadenzaFattura


class Command(BaseCommand):
    help = 'Invia promemoria scadenze fatture cliente per email.'

    def add_arguments(self, parser):
        parser.add_argument('--giorni', type=int, default=7,
                            help='Finestra in giorni (default 7).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Non invia, stampa solo cosa farebbe.')
        parser.add_argument('--admin', action='store_true',
                            help='Invia anche un riepilogo a settings.DEFAULT_FROM_EMAIL.')

    def handle(self, *args, **opts):
        giorni = opts['giorni']
        dry = opts['dry_run']
        oggi = now().date()
        finestra_futuro = oggi + timedelta(days=giorni)
        finestra_passato = oggi - timedelta(days=giorni)

        scadenze = (
            ScadenzaFattura.objects
            .select_related('fattura__cliente')
            .filter(fattura__pagata=False)
            .filter(data__gte=finestra_passato, data__lte=finestra_futuro)
            .order_by('fattura__cliente_id', 'data')
        )

        # Raggruppo per cliente
        per_cliente: dict = {}
        for s in scadenze:
            cli = s.fattura.cliente
            if cli is None:
                continue
            per_cliente.setdefault(cli.pk, {'cliente': cli, 'scadenze': []})['scadenze'].append(s)

        inviate = 0
        saltate = 0
        for entry in per_cliente.values():
            cli = entry['cliente']
            to_addr = (cli.pec or '').strip()
            if not to_addr:
                primary = cli.contatti.filter(primary=True).first()
                if primary and primary.email:
                    to_addr = primary.email
            if not to_addr:
                saltate += 1
                self.stdout.write(self.style.WARNING(
                    f'  saltato {cli.ragione_sociale}: no email'
                ))
                continue

            righe = []
            totale = Decimal('0')
            for s in entry['scadenze']:
                giorni_diff = (s.data - oggi).days
                stato = (
                    'scaduta da %d giorni' % abs(giorni_diff)
                    if giorni_diff < 0
                    else f'scade tra {giorni_diff} giorni'
                ) if giorni_diff != 0 else 'scade oggi'
                righe.append(
                    f'  · Fattura {s.fattura.numero}/{s.fattura.anno}'
                    f' — {s.data.strftime("%d/%m/%Y")} — {s.importo} € ({stato})'
                )
                totale += s.importo

            body = (
                f'Gentile {cli.ragione_sociale},\n\n'
                f'Le ricordiamo le seguenti scadenze fatture in essere:\n\n'
                + '\n'.join(righe)
                + f'\n\nTotale: {totale} €\n\n'
                'Per qualsiasi chiarimento, restiamo a Sua disposizione.\n'
            )
            subject = f'Promemoria scadenze fatture — {len(righe)} posizioni'

            if dry:
                self.stdout.write(f'  [DRY] {to_addr}: {len(righe)} scadenze, totale {totale}')
            else:
                try:
                    EmailMessage(
                        subject=subject,
                        body=body,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        to=[to_addr],
                    ).send(fail_silently=False)
                    inviate += 1
                    self.stdout.write(self.style.SUCCESS(
                        f'  → {to_addr}: {len(righe)} scadenze, totale {totale} €'
                    ))
                except Exception as exc:
                    saltate += 1
                    self.stdout.write(self.style.ERROR(
                        f'  errore invio a {to_addr}: {exc}'
                    ))

        if opts['admin'] and not dry and inviate:
            send_mail(
                subject=f'Promemoria scadenze: {inviate} email inviate',
                message=(
                    f'Riepilogo promemoria scadenze al {oggi.strftime("%d/%m/%Y")}:\n\n'
                    f'  - email inviate: {inviate}\n'
                    f'  - clienti saltati (no email): {saltate}\n'
                    f'  - finestra: ±{giorni} giorni\n'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[settings.DEFAULT_FROM_EMAIL],
                fail_silently=True,
            )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Completato: {inviate} email inviate, {saltate} saltate.'
        ))
