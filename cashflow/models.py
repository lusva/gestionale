"""Modelli per il modulo Cashflow.

Tre entità principali:

- ``ScadenzaFiscale`` — scadenze fiscali manuali (IVA, F24, INPS, ecc.).
- ``SpesaRicorrente`` — template di una spesa ripetuta nel tempo (abbonamenti,
  affitti, leasing). Genera automaticamente le ``ScadenzaSpesa`` future.
- ``ScadenzaSpesa`` — singola istanza di scadenza generata da una
  ``SpesaRicorrente``; tiene importo e stato di pagamento individuali.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.db import models


class TipoScadenzaFiscale(models.TextChoices):
    IVA = 'iva', 'IVA'
    F24 = 'f24', 'F24'
    INPS = 'inps', 'INPS'
    INAIL = 'inail', 'INAIL'
    IRES = 'ires', 'IRES'
    IRAP = 'irap', 'IRAP'
    ENASARCO = 'enasarco', 'ENASARCO'
    RITENUTE = 'ritenute', 'Ritenute'
    ALTRO = 'altro', 'Altro'


class ScadenzaFiscale(models.Model):
    """Scadenza fiscale inserita manualmente.

    Esempi: liquidazione IVA mensile/trimestrale, F24 acconti, contributi
    INPS, ritenute d'acconto. Non genera nulla in automatico.
    """

    data_scadenza = models.DateField()
    tipo = models.CharField(
        max_length=20, choices=TipoScadenzaFiscale.choices,
        default=TipoScadenzaFiscale.ALTRO,
    )
    descrizione = models.CharField(max_length=200)
    importo = models.DecimalField(max_digits=12, decimal_places=2)
    pagata = models.BooleanField(default=False)
    data_pagamento = models.DateField(blank=True, null=True)
    note = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['data_scadenza']
        verbose_name = 'Scadenza fiscale'
        verbose_name_plural = 'Scadenze fiscali'

    def __str__(self):
        return f'{self.get_tipo_display()} — {self.descrizione} ({self.data_scadenza})'


class Periodicita(models.TextChoices):
    MENSILE = 'mensile', 'Mensile'
    BIMESTRALE = 'bimestrale', 'Bimestrale'
    TRIMESTRALE = 'trimestrale', 'Trimestrale'
    QUADRIMESTRALE = 'quadrimestrale', 'Quadrimestrale'
    SEMESTRALE = 'semestrale', 'Semestrale'
    ANNUALE = 'annuale', 'Annuale'


_MESI_PER_PERIODO = {
    Periodicita.MENSILE: 1,
    Periodicita.BIMESTRALE: 2,
    Periodicita.TRIMESTRALE: 3,
    Periodicita.QUADRIMESTRALE: 4,
    Periodicita.SEMESTRALE: 6,
    Periodicita.ANNUALE: 12,
}


class SpesaRicorrente(models.Model):
    """Template di spesa ricorrente.

    Da un template si generano N ``ScadenzaSpesa`` future (vedi
    ``genera_scadenze``). La generazione è idempotente: se esiste già una
    scadenza per quella data, non se ne crea un'altra.
    """

    descrizione = models.CharField(max_length=200)
    importo = models.DecimalField(max_digits=12, decimal_places=2)
    periodicita = models.CharField(
        max_length=20, choices=Periodicita.choices,
        default=Periodicita.MENSILE,
    )
    giorno_del_mese = models.PositiveSmallIntegerField(
        default=1,
        help_text='Giorno del mese in cui cade la scadenza (1-31). '
                  'Se il mese ha meno giorni, viene troncato all\'ultimo.',
    )
    data_inizio = models.DateField()
    data_fine = models.DateField(blank=True, null=True)
    categoria_costo = models.ForeignKey(
        'anagrafiche.CategoriaCosto', on_delete=models.SET_NULL,
        blank=True, null=True, related_name='spese_ricorrenti',
    )
    fornitore = models.ForeignKey(
        'anagrafiche.Fornitore', on_delete=models.SET_NULL,
        blank=True, null=True, related_name='spese_ricorrenti',
    )
    attiva = models.BooleanField(default=True)
    note = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-attiva', 'descrizione']
        verbose_name = 'Spesa ricorrente'
        verbose_name_plural = 'Spese ricorrenti'

    def __str__(self):
        return f'{self.descrizione} ({self.get_periodicita_display()})'

    def _step_mesi(self) -> int:
        return _MESI_PER_PERIODO[self.periodicita]

    @staticmethod
    def _giorno_in_mese(anno: int, mese: int, giorno: int) -> date:
        last = monthrange(anno, mese)[1]
        return date(anno, mese, min(giorno, last))

    def date_attese(self, fino_a: date) -> list[date]:
        """Date di scadenza attese da ``data_inizio`` a ``fino_a``.

        Tiene conto di ``periodicita`` e ``giorno_del_mese``. Se è impostata
        una ``data_fine``, non genera oltre quella.
        """
        if not self.attiva:
            return []
        fine = min(fino_a, self.data_fine) if self.data_fine else fino_a
        if fine < self.data_inizio:
            return []
        out: list[date] = []
        anno, mese = self.data_inizio.year, self.data_inizio.month
        step = self._step_mesi()
        while True:
            d = self._giorno_in_mese(anno, mese, self.giorno_del_mese)
            if d > fine:
                break
            if d >= self.data_inizio:
                out.append(d)
            mese += step
            while mese > 12:
                mese -= 12
                anno += 1
        return out

    def genera_scadenze(self, fino_a: date) -> int:
        """Crea le ``ScadenzaSpesa`` mancanti fino alla data indicata.

        Non sovrascrive le scadenze esistenti — l'utente potrebbe averle
        modificate o segnate come pagate. Ritorna quante ne sono state create.
        """
        date_attese = self.date_attese(fino_a)
        esistenti = set(self.scadenze.values_list('data_scadenza', flat=True))
        nuove = 0
        for d in date_attese:
            if d in esistenti:
                continue
            ScadenzaSpesa.objects.create(
                spesa=self, data_scadenza=d, importo=self.importo,
            )
            nuove += 1
        return nuove


class ScadenzaSpesa(models.Model):
    """Singola scadenza generata da una ``SpesaRicorrente``.

    L'importo è copiato dal template alla creazione: se il template cambia
    il futuro non riscrive il passato.
    """

    spesa = models.ForeignKey(
        SpesaRicorrente, on_delete=models.CASCADE, related_name='scadenze',
    )
    data_scadenza = models.DateField()
    importo = models.DecimalField(max_digits=12, decimal_places=2)
    pagata = models.BooleanField(default=False)
    data_pagamento = models.DateField(blank=True, null=True)
    note = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['data_scadenza']
        verbose_name = 'Scadenza spesa ricorrente'
        verbose_name_plural = 'Scadenze spese ricorrenti'
        constraints = [
            models.UniqueConstraint(
                fields=['spesa', 'data_scadenza'],
                name='cashflow_scadenzaspesa_unique_per_data',
            ),
        ]

    def __str__(self):
        return f'{self.spesa.descrizione} — {self.data_scadenza} — {self.importo}'

    @property
    def importo_residuo(self) -> Decimal:
        if self.pagata:
            return Decimal('0')
        return self.importo
