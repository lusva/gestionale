"""Signal handlers che collegano eventi del dominio al webhook dispatcher."""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from clienti.models import Cliente
from opportunita.models import Opportunita, Stadio

from .models import WebhookEvento
from .serializers import cliente_to_dict, opportunita_to_dict
from .webhooks import dispatch


@receiver(post_save, sender=Cliente)
def _cliente_saved(sender, instance, created, **kwargs):
    evento = WebhookEvento.CLIENTE_CREATO if created else WebhookEvento.CLIENTE_MODIFICATO
    dispatch(evento, cliente_to_dict(instance))


@receiver(post_delete, sender=Cliente)
def _cliente_deleted(sender, instance, **kwargs):
    dispatch(WebhookEvento.CLIENTE_ELIMINATO, cliente_to_dict(instance))


@receiver(post_save, sender=Opportunita)
def _opp_saved(sender, instance, created, **kwargs):
    if created:
        dispatch(WebhookEvento.OPP_CREATA, opportunita_to_dict(instance))
        return
    # Stadio speciale → evento dedicato oltre al generico
    if instance.stadio == Stadio.CHIUSA_WIN:
        dispatch(WebhookEvento.OPP_CHIUSA_WIN, opportunita_to_dict(instance))
    elif instance.stadio == Stadio.CHIUSA_LOST:
        dispatch(WebhookEvento.OPP_CHIUSA_LOST, opportunita_to_dict(instance))
    else:
        dispatch(WebhookEvento.OPP_MODIFICATA, opportunita_to_dict(instance))


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
        TestataDdt, TestataFattura, TestataNotaCredito,
        TestataOfferta, TestataOrdine,
    )

    @receiver(post_save, sender=TestataFattura, weak=False, dispatch_uid='wh_fattura')
    def _fattura(sender, instance, created, **kwargs):
        if created:
            dispatch(WebhookEvento.FATTURA_CREATA, _testata_to_dict(instance))
        else:
            dispatch(WebhookEvento.FATTURA_MODIFICATA, _testata_to_dict(instance))
            if instance.pagata:
                dispatch(WebhookEvento.FATTURA_PAGATA, _testata_to_dict(instance))

    @receiver(post_save, sender=TestataOfferta, weak=False, dispatch_uid='wh_offerta')
    def _offerta(sender, instance, created, **kwargs):
        if created:
            dispatch(WebhookEvento.OFFERTA_CREATA, _testata_to_dict(instance))

    @receiver(post_save, sender=TestataOrdine, weak=False, dispatch_uid='wh_ordine')
    def _ordine(sender, instance, created, **kwargs):
        if created:
            dispatch(WebhookEvento.ORDINE_CREATO, _testata_to_dict(instance))

    @receiver(post_save, sender=TestataDdt, weak=False, dispatch_uid='wh_ddt')
    def _ddt(sender, instance, created, **kwargs):
        if created:
            dispatch(WebhookEvento.DDT_CREATO, _testata_to_dict(instance))

    @receiver(post_save, sender=TestataNotaCredito, weak=False, dispatch_uid='wh_nc')
    def _nc(sender, instance, created, **kwargs):
        if created:
            dispatch(WebhookEvento.NOTA_CREDITO_CREATA, _testata_to_dict(instance))


_connect_doc_signals()
