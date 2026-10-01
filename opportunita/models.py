from django.conf import settings
from django.db import models
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
]


class Opportunita(models.Model):
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
        verbose_name = 'Opportunità'
        verbose_name_plural = 'Opportunità'
        ordering = ['ordine', '-updated_at']

    def __str__(self):
        return f'{self.titolo} — {self.cliente}'

    def save(self, *args, **kwargs):
        # data_chiusura: valorizzata all'ingresso in uno stadio chiuso,
        # azzerata se l'opportunità viene riaperta.
        if self.stadio in (Stadio.CHIUSA_WIN, Stadio.CHIUSA_LOST):
            if self.pk and not self._state.adding:
                prev_stadio = (
                    type(self).objects.filter(pk=self.pk)
                    .values_list('stadio', flat=True).first()
                )
                if prev_stadio is not None and prev_stadio != self.stadio:
                    self.data_chiusura = timezone.localdate()
            if self.data_chiusura is None:
                self.data_chiusura = timezone.localdate()
        else:
            self.data_chiusura = None
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and 'stadio' in update_fields:
            kwargs['update_fields'] = set(update_fields) | {'data_chiusura'}
        super().save(*args, **kwargs)

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

    def cambia_stadio(self, nuovo):
        if nuovo == self.stadio:
            return
        self.stadio = nuovo
        self.ingresso_stadio = timezone.now()
        if nuovo == Stadio.CHIUSA_WIN:
            self.probabilita = 100
        elif nuovo == Stadio.CHIUSA_LOST:
            self.probabilita = 0
        self.save()
