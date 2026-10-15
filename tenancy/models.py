"""Shared company boundary for every business model.

The SQL expression is resolved at execution time: querysets declared on a view
or ModelChoiceField at module import must not capture the first visitor's scope.
``all_objects`` is reserved for authentication, provisioning and migrations.
"""
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, router

from .context import current_azienda_id


class Azienda(models.Model):
    nome = models.CharField(max_length=200)
    attiva = models.BooleanField(default=True)
    configurata = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Aziende'

    def __str__(self):
        return self.nome


class CurrentAzienda(models.Expression):
    output_field = models.BigIntegerField()

    def as_sql(self, compiler, connection):
        return '%s', [current_azienda_id()]


def _check_relation(field, value, azienda_id):
    if value is None or not field.is_relation or field.many_to_many:
        return
    model = field.remote_field.model
    pk = getattr(value, 'pk', value)
    if hasattr(pk, 'resolve_expression'):
        raise ValidationError({field.name: 'Assegnazione relazionale non consentita.'})
    if issubclass(model, TenantModel):
        valid = model.all_objects.filter(pk=pk, azienda_id=azienda_id).exists()
    elif model._meta.label_lower == settings.AUTH_USER_MODEL.lower():
        valid = model.objects.filter(pk=pk, profile__azienda_id=azienda_id).exists()
    else:
        return
    if not valid:
        raise ValidationError({field.name: 'Il riferimento non appartiene alla tua azienda.'})


class TenantQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if 'azienda' in kwargs or 'azienda_id' in kwargs:
            raise PermissionDenied('Non è possibile trasferire dati fra aziende.')
        if self.model._meta.label_lower == 'accounts.profile' and {'user', 'user_id'} & kwargs.keys():
            raise PermissionDenied('Non è possibile riassegnare il profilo a un altro utente.')
        for name, value in kwargs.items():
            field = self.model._meta.get_field(name)
            _check_relation(field, value, current_azienda_id())
        return super().update(**kwargs)

    def bulk_create(self, objs, **kwargs):
        objs = list(objs)
        # ON CONFLICT can target globally unique keys (Profile.user or token
        # hashes) without honoring this queryset's WHERE clause.
        if kwargs.get('update_conflicts'):
            raise PermissionDenied('Aggiornamenti massivi su conflitto non consentiti.')
        for obj in objs:
            obj.check_azienda()
        if any(f in {'azienda', 'azienda_id'} for f in kwargs.get('update_fields') or []):
            raise PermissionDenied('Non è possibile trasferire dati fra aziende.')
        return super().bulk_create(objs, **kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        objs = list(objs)
        if any(f in {'azienda', 'azienda_id'} for f in fields):
            raise PermissionDenied('Non è possibile trasferire dati fra aziende.')
        for obj in objs:
            obj.check_azienda()
        # Django implements this with CASE expressions; related-field CASE
        # writes are deliberately unsupported rather than bypass validation.
        return super().bulk_update(objs, fields, batch_size=batch_size)


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(azienda_id=CurrentAzienda())


class TenantModel(models.Model):
    azienda = models.ForeignKey(
        Azienda, on_delete=models.PROTECT, editable=False,
        default=current_azienda_id, related_name='+',
    )
    objects = TenantManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True
        base_manager_name = 'objects'
        default_manager_name = 'objects'

    def check_azienda(self):
        azienda_id = current_azienda_id()
        if azienda_id is None or self.azienda_id != azienda_id:
            raise PermissionDenied('Accesso ai dati di questa azienda non consentito.')
        # A supplied primary key must never overwrite another company's row.
        if self.pk is not None and type(self).all_objects.filter(pk=self.pk).exclude(
            azienda_id=azienda_id,
        ).exists():
            raise PermissionDenied('Accesso ai dati di questa azienda non consentito.')
        for field in self._meta.concrete_fields:
            if field.name != 'azienda' and field.is_relation:
                if self._meta.label_lower == 'accounts.profile' and field.name == 'user':
                    # Creating the membership itself cannot require a profile
                    # that has not yet been inserted.
                    continue
                _check_relation(field, getattr(self, field.attname), azienda_id)

    def save(self, *args, **kwargs):
        self.check_azienda()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.check_azienda()
        return super().delete(*args, **kwargs)

    def validate_constraints(self, exclude=None):
        # azienda is non-editable and excluded by ModelForm, but is necessary
        # to validate scoped unique constraints before the INSERT.
        exclude = set(exclude or ()) - {'azienda'}
        errors = {}
        using = router.db_for_write(type(self), instance=self)
        for model, constraints in self.get_constraints():
            for constraint in constraints:
                try:
                    constraint.validate(model, self, exclude=exclude, using=using)
                except ValidationError as exc:
                    fields = getattr(constraint, 'fields', ())
                    if isinstance(constraint, models.UniqueConstraint) and len(fields) == 2 and fields[0] == 'azienda':
                        # Preserve the field error used by forms/API before
                        # the original unique field became company-scoped.
                        field = fields[1]
                        exc = ValidationError({field: self.unique_error_message(model, (field,))})
                    errors = exc.update_error_dict(errors)
        if errors:
            raise ValidationError(errors)
