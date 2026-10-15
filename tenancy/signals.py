from django.core.exceptions import PermissionDenied
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from .context import current_azienda_id
from .models import TenantModel


@receiver(m2m_changed)
def protect_company_relations(sender, instance, action, reverse, model, pk_set, **kwargs):
    if action not in {'pre_add', 'pre_remove', 'pre_clear'}:
        return
    if isinstance(instance, TenantModel):
        instance.check_azienda()
    if action == 'pre_add' and pk_set and issubclass(model, TenantModel):
        if model.all_objects.filter(
            pk__in=pk_set, azienda_id=current_azienda_id(),
        ).count() != len(pk_set):
            raise PermissionDenied('Relazioni tra aziende diverse non consentite.')
