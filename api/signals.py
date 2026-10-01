"""Signal handlers che collegano eventi del dominio al webhook dispatcher.

I payload dei salvataggi si costruiscono a commit avvenuto, rileggendo
l'oggetto dal DB (``dispatch`` accetta una funzione): una fattura appena
creata ha così righe e totali, e un salvataggio annullato non manda nulla.
Gli eventi di stato (``fattura.pagata``, ``opportunita.chiusa_*``) partono
solo al passaggio in quello stato, non a ogni salvataggio successivo.
"""
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from clienti.models import Cliente
from opportunita.models import Opportunita, Stadio

from .models import WebhookEvento
from .serializers import cliente_to_dict, opportunita_to_dict
from .webhooks import dispatch


def _valore_precedente(sender, instance, campo):
    """Il valore di ``campo`` ancora nel DB (None per un oggetto nuovo)."""
    if instance.pk is None or instance._state.adding:
        return None
    return sender.objects.filter(pk=instance.pk).values_list(campo, flat=True).first()


def _riletto(model, pk, serializza, related=()):
    """Payload differito: rilegge l'oggetto al commit (None se sparito)."""
    def costruisci():
        obj = model.objects.select_related(*related).filter(pk=pk).first()
        return serializza(obj) if obj is not None else None
    return costruisci


@receiver(post_save, sender=Cliente)
def _cliente_saved(sender, instance, created, **kwargs):
    evento = WebhookEvento.CLIENTE_CREATO if created else WebhookEvento.CLIENTE_MODIFICATO
    dispatch(evento, _riletto(Cliente, instance.pk, cliente_to_dict, ('settore',)))


@receiver(post_delete, sender=Cliente)
def _cliente_deleted(sender, instance, **kwargs):
    dispatch(WebhookEvento.CLIENTE_ELIMINATO, cliente_to_dict(instance))


@receiver(pre_save, sender=Opportunita)
def _opp_prima(sender, instance, **kwargs):
    instance._stadio_prima = _valore_precedente(sender, instance, 'stadio')


@receiver(post_save, sender=Opportunita)
def _opp_saved(sender, instance, created, **kwargs):
    payload = _riletto(Opportunita, instance.pk, opportunita_to_dict, ('cliente', 'owner'))
    if created:
        dispatch(WebhookEvento.OPP_CREATA, payload)
        return
    prima = getattr(instance, '_stadio_prima', None)
    # Stadio di chiusura → evento dedicato, ma solo quando ci si entra
    if instance.stadio == Stadio.CHIUSA_WIN and prima != Stadio.CHIUSA_WIN:
        dispatch(WebhookEvento.OPP_CHIUSA_WIN, payload)
    elif instance.stadio == Stadio.CHIUSA_LOST and prima != Stadio.CHIUSA_LOST:
        dispatch(WebhookEvento.OPP_CHIUSA_LOST, payload)
    else:
        dispatch(WebhookEvento.OPP_MODIFICATA, payload)


# === Documenti ===

def _testata_to_dict(instance) -> dict:
    """Serializzazione minima testata per webhook payload."""
    base = {
        'id': instance.pk,
        'modello': instance.__class__.__name__,
        'numero': instance.numero,
        'anno': instance.anno,
        'data_documento': instance.data_documento.isoformat() if instance.data_documento else None,
        'imponibile': str(instance.imponibile) if instance.imponibile is not None else None,
    }
    if hasattr(instance, 'totale_documento'):
        # fattura di acquisto: totali salvati dall'import
        base['imposta'] = str(instance.imposta) if instance.imposta is not None else None
        base['totale'] = str(instance.totale_documento) if instance.totale_documento is not None else None
    else:
        from documenti.utils import calcola_valori

        v = calcola_valori(instance)
        base['imponibile'] = str(v['imponibile'])
        base['imposta'] = str(v['imposta_totale'])
        base['totale'] = str(v['totale'])
    if hasattr(instance, 'cliente_id') and instance.cliente_id:
        base['cliente'] = {
            'id': instance.cliente_id,
            'ragione_sociale': str(instance.cliente),
        }
    if hasattr(instance, 'fornitore_id') and instance.fornitore_id:
        base['fornitore'] = {
            'id': instance.fornitore_id,
            'ragione_sociale': str(instance.fornitore),
        }
    if hasattr(instance, 'numero_fornitore'):
        base['numero_fornitore'] = instance.numero_fornitore
    if hasattr(instance, 'tipo_documento'):
        base['tipo_documento'] = instance.tipo_documento
    if hasattr(instance, 'sezionale'):
        base['sezionale'] = instance.sezionale
    if hasattr(instance, 'pagata'):
        base['pagata'] = instance.pagata
    return base


def _connect_doc_signals():
    """Connette i signal sui modelli ``documenti`` (lazy: i modelli vivono
    in un'altra app, quindi va fatto a importazione modulo)."""
    from documenti.models import (
        TestataDdt, TestataFattura, TestataFatturaAcquisto, TestataNotaCredito,
        TestataOfferta, TestataOrdine,
    )

    def testata(model, pk):
        return _riletto(model, pk, _testata_to_dict)

    @receiver(pre_save, sender=TestataFattura, weak=False, dispatch_uid='wh_fattura_prima')
    def _fattura_prima(sender, instance, **kwargs):
        instance._pagata_prima = _valore_precedente(sender, instance, 'pagata')

    @receiver(post_save, sender=TestataFattura, weak=False, dispatch_uid='wh_fattura')
    def _fattura(sender, instance, created, **kwargs):
        payload = testata(TestataFattura, instance.pk)
        if created:
            dispatch(WebhookEvento.FATTURA_CREATA, payload)
        elif instance.pagata and not getattr(instance, '_pagata_prima', False):
            dispatch(WebhookEvento.FATTURA_PAGATA, payload)
        else:
            dispatch(WebhookEvento.FATTURA_MODIFICATA, payload)

    @receiver(post_save, sender=TestataOfferta, weak=False, dispatch_uid='wh_offerta')
    def _offerta(sender, instance, created, **kwargs):
        if created:
            dispatch(WebhookEvento.OFFERTA_CREATA, testata(TestataOfferta, instance.pk))

    @receiver(post_save, sender=TestataOrdine, weak=False, dispatch_uid='wh_ordine')
    def _ordine(sender, instance, created, **kwargs):
        if created:
            dispatch(WebhookEvento.ORDINE_CREATO, testata(TestataOrdine, instance.pk))

    @receiver(post_save, sender=TestataDdt, weak=False, dispatch_uid='wh_ddt')
    def _ddt(sender, instance, created, **kwargs):
        if created:
            dispatch(WebhookEvento.DDT_CREATO, testata(TestataDdt, instance.pk))

    @receiver(post_save, sender=TestataNotaCredito, weak=False, dispatch_uid='wh_nc')
    def _nc(sender, instance, created, **kwargs):
        if created:
            dispatch(WebhookEvento.NOTA_CREDITO_CREATA, testata(TestataNotaCredito, instance.pk))

    @receiver(post_save, sender=TestataFatturaAcquisto, weak=False, dispatch_uid='wh_fattura_acq')
    def _fattura_acquisto(sender, instance, created, **kwargs):
        if created:
            dispatch(WebhookEvento.FATTURA_ACQUISTO_IMPORTATA,
                     testata(TestataFatturaAcquisto, instance.pk))


_connect_doc_signals()
