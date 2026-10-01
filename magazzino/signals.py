"""
Signal handler: post_save su ``documenti.RigaDocumento`` → crea/aggiorna
il Movimento corrispondente. Tipo dedotto dalla testata cui appartiene
la riga:
  - testata_ddt_fornitore → Carico (C)
  - testata_ddt           → Scarico (S)
  - testata_fattura       → Scarico (S), salvo merce già uscita con DDT
  - testata_ordine        → Impegnato (I)
  - testata_offerta       → Offerto (O)
  - testata_nota_credito  → Carico (C) [reso merce]

Regola anti doppio scarico: la merce esce fisicamente con il DDT. Una riga
di fattura scarica solo se la fattura NON è collegata (tramite l'ordine di
origine, ``TestataOrdine.fattura_collegata`` / ``ddt_collegato``) a un DDT
non annullato; se lo è, lo scarico l'ha già fatto il DDT (fattura
differita). Il collegamento può nascere prima o dopo le righe (DDT emesso
dopo la fattura, collegamenti fatti a mano…): i signal sul M2M e sul DDT
risincronizzano le righe delle fatture coinvolte.

Nota di credito: non c'è modo di distinguere un reso merce da una
rettifica di prezzo, quindi ogni riga con articolo resta un Carico.

Se l'articolo della riga non è valorizzato (o la riga non genera più un
movimento) l'eventuale Movimento esistente viene eliminato. Se il modulo
magazzino è disattivato sull'AnagraficaAzienda, non si fa nulla.

La data del movimento è la data del documento: se questa cambia sulla
testata, i movimenti delle sue righe vengono aggiornati.
"""
from __future__ import annotations

from datetime import datetime

from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from documenti.models import (
    RigaDocumento,
    TestataDdt,
    TestataDdtFornitore,
    TestataFattura,
    TestataNotaCredito,
    TestataOfferta,
    TestataOrdine,
    _modulo_magazzino_attivo,
)

from .models import Movimento


# testata → FK della riga
_FK_RIGA = {
    TestataDdtFornitore: 'testata_ddt_fornitore',
    TestataDdt: 'testata_ddt',
    TestataFattura: 'testata_fattura',
    TestataNotaCredito: 'testata_nota_credito',
    TestataOrdine: 'testata_ordine',
    TestataOfferta: 'testata_offerta',
}


def _fattura_con_ddt(fattura_id) -> bool:
    """True se la merce della fattura è già uscita con un DDT (non annullato)
    dello stesso ordine di origine."""
    return TestataDdt.objects.filter(
        ordini_origine__fattura_collegata__pk=fattura_id,
    ).exclude(stato=TestataDdt.StatoDdt.ANNULLATO).exists()


def _tipo_e_data(riga):
    """Ritorna (tipo, data_documento) oppure (None, None) se la riga non genera movimenti."""
    if not riga.articolo_id:
        return None, None
    if riga.testata_ddt_fornitore_id:
        return 'C', riga.testata_ddt_fornitore.data_documento
    if riga.testata_ddt_id:
        return 'S', riga.testata_ddt.data_documento
    if riga.testata_fattura_id:
        if _fattura_con_ddt(riga.testata_fattura_id):
            return None, None
        return 'S', riga.testata_fattura.data_documento
    if riga.testata_nota_credito_id:
        return 'C', riga.testata_nota_credito.data_documento
    if riga.testata_ordine_id:
        return 'I', riga.testata_ordine.data_documento
    if riga.testata_offerta_id:
        return 'O', riga.testata_offerta.data_documento
    return None, None


def _solo_data(valore):
    return valore.date() if isinstance(valore, datetime) else valore


@receiver(post_save, sender=RigaDocumento)
def on_riga_saved(sender, instance, **kwargs):
    if not _modulo_magazzino_attivo():
        return
    tipo, data = _tipo_e_data(instance)
    if tipo is None:
        # articolo tolto o merce già scaricata dal DDT: niente movimento
        Movimento.objects.filter(riga_documento_id=instance.pk).delete()
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
            'data': _solo_data(data),
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


def sincronizza_fatture(fattura_ids) -> None:
    """Ricalcola i movimenti delle righe delle fatture indicate (dopo un
    cambio nella catena ordine → DDT / fattura)."""
    ids = {pk for pk in fattura_ids if pk}
    if not ids or not _modulo_magazzino_attivo():
        return
    for riga in RigaDocumento.objects.filter(testata_fattura_id__in=ids).select_related(
            'articolo', 'testata_fattura'):
        on_riga_saved(RigaDocumento, riga)


def _fatture_degli_ordini(ordine_ids):
    return set(TestataOrdine.fattura_collegata.through.objects.filter(
        testataordine_id__in=ordine_ids,
    ).values_list('testatafattura_id', flat=True))


def _fatture_coinvolte(instance, model, pk_set):
    """Le fatture i cui movimenti dipendono dal collegamento M2M modificato."""
    if isinstance(instance, TestataOrdine):
        ordini = {instance.pk}
    elif isinstance(instance, TestataFattura):
        return {instance.pk}
    elif model is TestataOrdine:          # lato DDT: pk_set sono ordini
        ordini = set(pk_set or ())
        ordini |= set(instance.ordini_origine.values_list('pk', flat=True))
    else:
        ordini = set()
    fatture = _fatture_degli_ordini(ordini)
    if model is TestataFattura:           # lato ordine di fattura_collegata
        fatture |= set(pk_set or ())
    return fatture


def _on_catena_changed(sender, instance, action, model, pk_set, **kwargs):
    if action == 'pre_clear':
        # dopo il clear il collegamento non c'è più: lo annoto prima
        instance._magazzino_fatture = _fatture_coinvolte(instance, model, None)
        if isinstance(instance, TestataOrdine) and model is TestataFattura:
            instance._magazzino_fatture |= set(
                instance.fattura_collegata.values_list('pk', flat=True))
        return
    if action not in ('post_add', 'post_remove', 'post_clear'):
        return
    fatture = _fatture_coinvolte(instance, model, pk_set)
    if action == 'post_clear':
        fatture |= getattr(instance, '_magazzino_fatture', set())
    sincronizza_fatture(fatture)


m2m_changed.connect(_on_catena_changed, sender=TestataOrdine.ddt_collegato.through,
                    dispatch_uid='magazzino_ordine_ddt')
m2m_changed.connect(_on_catena_changed, sender=TestataOrdine.fattura_collegata.through,
                    dispatch_uid='magazzino_ordine_fattura')


def on_testata_saved(sender, instance, created=False, **kwargs):
    """Data del documento cambiata → aggiorna la data dei suoi movimenti.
    Per un DDT (anche annullato/ripristinato) risincronizza le fatture dei
    suoi ordini."""
    if created:
        return
    fk = _FK_RIGA[sender]
    data = _solo_data(instance.data_documento)
    Movimento.objects.filter(**{f'riga_documento__{fk}_id': instance.pk}).exclude(
        data=data,
    ).update(data=data)
    if sender is TestataDdt:
        ordini = instance.ordini_origine.values_list('pk', flat=True)
        sincronizza_fatture(_fatture_degli_ordini(set(ordini)))


for _testata in _FK_RIGA:
    post_save.connect(on_testata_saved, sender=_testata,
                      dispatch_uid=f'magazzino_data_{_testata.__name__}')
