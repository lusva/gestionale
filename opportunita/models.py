from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from tenancy.models import TenantModel
from django.urls import reverse
from django.utils import timezone


class Stadio(models.TextChoices):
    NUOVA = 'nuova', 'Nuova'
    QUALIFICATA = 'qualificata', 'Qualificata'
    PROPOSTA = 'proposta', 'Proposta'
    NEGOZIAZIONE = 'negoziazione', 'Negoziazione'
    CHIUSA_WIN = 'chiusa_win', 'Chiusa vinta'
    CHIUSA_LOST = 'chiusa_lost', 'Chiusa persa'


STADIO_COLORS = {
    Stadio.NUOVA: 'var(--ink-3)',
    Stadio.QUALIFICATA: 'var(--info)',
    Stadio.PROPOSTA: 'var(--warn)',
    Stadio.NEGOZIAZIONE: 'var(--brand-violet)',
    Stadio.CHIUSA_WIN: 'var(--success)',
    Stadio.CHIUSA_LOST: 'var(--danger)',
}

PIPELINE_COLUMNS = [
    Stadio.NUOVA,
    Stadio.QUALIFICATA,
    Stadio.PROPOSTA,
    Stadio.NEGOZIAZIONE,
    Stadio.CHIUSA_WIN,
    Stadio.CHIUSA_LOST,
]


class Opportunita(TenantModel):
    cliente = models.ForeignKey(
        'clienti.Cliente', on_delete=models.CASCADE, related_name='opportunita',
    )
    titolo = models.CharField(max_length=200)
    descrizione = models.TextField(blank=True)
    valore = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    stadio = models.CharField(
        max_length=20, choices=Stadio.choices, default=Stadio.NUOVA,
    )
    probabilita = models.PositiveIntegerField(default=25, help_text='0-100')
    chiusura_prevista = models.DateField(null=True, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='opportunita_owned',
    )
    ingresso_stadio = models.DateTimeField(default=timezone.now)
    ordine = models.PositiveIntegerField(
        default=0,
        help_text='Posizione nella colonna Kanban (più basso = più in alto)',
    )
    data_chiusura = models.DateField(
        null=True, blank=True, editable=False,
        help_text='Data di passaggio a Chiusa vinta/persa (valorizzata automaticamente).',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        verbose_name = 'Opportunità'
        verbose_name_plural = 'Opportunità'
        ordering = ['ordine', '-updated_at']

    def __str__(self):
        return f'{self.titolo} — {self.cliente}'

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        # Un unico punto di normalizzazione per form, API e trascinamento.
        # Un aggiornamento di soli campi estranei allo stadio non lo modifica.
        if update_fields is None or {'stadio', 'probabilita'} & set(update_fields):
            previous = None
            if self.pk and not self._state.adding:
                previous = type(self).objects.filter(pk=self.pk).values_list('stadio', flat=True).first()
            persisted_stage = self.stadio if update_fields is None or 'stadio' in update_fields else previous
            stage_changed = previous is not None and previous != persisted_stage
            if stage_changed:
                self.ingresso_stadio = timezone.now()
            if persisted_stage in (Stadio.CHIUSA_WIN, Stadio.CHIUSA_LOST):
                self.probabilita = 100 if persisted_stage == Stadio.CHIUSA_WIN else 0
                if stage_changed or self.data_chiusura is None:
                    self.data_chiusura = timezone.localdate()
            else:
                self.data_chiusura = None
                if previous in (Stadio.CHIUSA_WIN, Stadio.CHIUSA_LOST) and self.probabilita in (0, 100):
                    self.probabilita = 25
            if update_fields is not None:
                kwargs['update_fields'] = set(update_fields) | {'data_chiusura', 'probabilita'}
                if stage_changed:
                    kwargs['update_fields'].add('ingresso_stadio')
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        errors = {}
        if self.probabilita is not None and not 0 <= self.probabilita <= 100:
            errors['probabilita'] = 'La probabilità deve essere compresa tra 0 e 100.'
        if self.valore is not None and self.valore < 0:
            errors['valore'] = 'Il valore non può essere negativo.'
        if errors:
            raise ValidationError(errors)

    def get_absolute_url(self):
        return reverse('opportunita:detail', args=[self.pk])

    @property
    def giorni_in_stadio(self):
        delta = timezone.now() - self.ingresso_stadio
        return max(delta.days, 0)

    @property
    def stadio_color(self):
        return STADIO_COLORS.get(self.stadio, 'var(--ink-3)')

    @property
    def is_chiusa(self):
        return self.stadio in (Stadio.CHIUSA_WIN, Stadio.CHIUSA_LOST)

    @property
    def in_stallo(self):
        return not self.is_chiusa and self.giorni_in_stadio >= 14

    @property
    def chiusura_in_ritardo(self):
        return not self.is_chiusa and self.chiusura_prevista is not None and self.chiusura_prevista < timezone.localdate()

    def cambia_stadio(self, nuovo):
        if nuovo not in Stadio.values:
            raise ValidationError({'stadio': 'Stadio non valido.'})
        if nuovo == self.stadio:
            return
        self.stadio = nuovo
        self.save(update_fields={'stadio', 'updated_at'})
