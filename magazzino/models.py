"""
Movimento di magazzino.

Creato automaticamente dal signal ``magazzino.signals.on_riga_saved`` in
risposta al post_save di ``documenti.RigaDocumento``. Può essere disattivato
impostando ``AnagraficaAzienda.modulo_magazzino_attivo = False``.
"""
from django.db import models


class Movimento(models.Model):
    class Tipo(models.TextChoices):
        CARICO = 'C', 'Carico (DDT fornitore)'
        SCARICO = 'S', 'Scarico (DDT / fattura)'
        IMPEGNATO = 'I', 'Impegnato (ordine)'
        OFFERTO = 'O', 'Offerto (offerta)'

    data = models.DateField(null=True, blank=True)
    descrizione = models.CharField(max_length=255, blank=True, default='')
    quantita = models.DecimalField(max_digits=10, decimal_places=2)
    prezzo = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True, default=0,
    )
    tipo = models.CharField(max_length=1, choices=Tipo.choices)
    articolo = models.ForeignKey(
        'anagrafiche.Articolo',
        on_delete=models.CASCADE,
        related_name='movimenti',
    )
    riga_documento = models.OneToOneField(
        'documenti.RigaDocumento',
        on_delete=models.CASCADE,
        related_name='movimento',
        null=True, blank=True,
    )

    def __str__(self):
        return f'{self.data} {self.tipo} {self.articolo} × {self.quantita}'

    class Meta:
        ordering = ['-data', '-id']
        indexes = [
            models.Index(fields=['tipo']),
            models.Index(fields=['articolo']),
        ]
