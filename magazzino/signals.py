"""
Signal handler: post_save su ``documenti.RigaDocumento`` → crea/aggiorna
il Movimento corrispondente. Tipo dedotto dalla testata cui appartiene
la riga:
  - testata_ddt_fornitore → Carico (C)
  - testata_ddt           → Scarico (S)
  - testata_fattura       → Scarico (S)
  - testata_ordine        → Impegnato (I)
  - testata_offerta       → Offerto (O)
  - testata_nota_credito  → Carico (C) [reso merce]

Se l'articolo della riga non è valorizzato o il modulo magazzino è
disattivato sull'AnagraficaAzienda, non si fa nulla.
"""
from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from documenti.models import RigaDocumento
from documenti.models import _modulo_magazzino_attivo

from .models import Movimento


def _tipo_e_data(riga):
    """Ritorna (tipo, data_documento) oppure (None, None) se testata sconosciuta."""
    if riga.testata_ddt_fornitore_id and riga.articolo_id:
        return 'C', riga.testata_ddt_fornitore.data_documento
    if riga.testata_ddt_id and riga.articolo_id:
        return 'S', riga.testata_ddt.data_documento
    if riga.testata_fattura_id and riga.articolo_id:
        return 'S', riga.testata_fattura.data_documento
    if riga.testata_nota_credito_id and riga.articolo_id:
        return 'C', riga.testata_nota_credito.data_documento
    if riga.testata_ordine_id and riga.articolo_id:
        return 'I', riga.testata_ordine.data_documento
    if riga.testata_offerta_id and riga.articolo_id:
        return 'O', riga.testata_offerta.data_documento
    return None, None


@receiver(post_save, sender=RigaDocumento)
def on_riga_saved(sender, instance, **kwargs):
    if not _modulo_magazzino_attivo():
        return
    tipo, data = _tipo_e_data(instance)
    if tipo is None:
        return
    prezzo = (
        instance.prezzo_acquisto
        if tipo == 'C' and instance.prezzo_acquisto
        else instance.importo_unitario
    )
    descrizione = instance.articolo.descrizione if instance.articolo else ''
    Movimento.objects.update_or_create(
        riga_documento=instance,
        defaults={
            'data': data,
            'articolo': instance.articolo,
            'descrizione': descrizione,
            'quantita': instance.quantita,
            'prezzo': prezzo,
            'tipo': tipo,
        },
    )


@receiver(post_delete, sender=RigaDocumento)
def on_riga_deleted(sender, instance, **kwargs):
    Movimento.objects.filter(riga_documento_id=instance.pk).delete()
