from django.conf import settings
from django.db import models
from django.urls import reverse


class TipoAttivita(models.TextChoices):
    CALL = 'call', 'Chiamata'
    EMAIL = 'email', 'Email'
    MEETING = 'meeting', 'Meeting'
    TASK = 'task', 'Task'
    NOTE = 'note', 'Nota'


TIPO_ICON = {
    TipoAttivita.CALL: 'phone',
    TipoAttivita.EMAIL: 'mail',
    TipoAttivita.MEETING: 'calendar',
    TipoAttivita.TASK: 'check',
    TipoAttivita.NOTE: 'edit',
}

TIPO_COLOR = {
    TipoAttivita.CALL: 'var(--warn)',
    TipoAttivita.EMAIL: 'var(--info)',
    TipoAttivita.MEETING: 'var(--brand-violet)',
    TipoAttivita.TASK: 'var(--success)',
    TipoAttivita.NOTE: 'var(--ink-3)',
}


class Attivita(models.Model):
    cliente = models.ForeignKey(
        'clienti.Cliente', on_delete=models.CASCADE, related_name='attivita',
        null=True, blank=True,
    )
    opportunita = models.ForeignKey(
        'opportunita.Opportunita', on_delete=models.CASCADE,
        related_name='attivita', null=True, blank=True,
    )
    tipo = models.CharField(
        max_length=10, choices=TipoAttivita.choices, default=TipoAttivita.TASK,
    )
    titolo = models.CharField(max_length=200)
    descrizione = models.TextField(blank=True)
    data = models.DateTimeField()
    durata_minuti = models.PositiveIntegerField(default=30)
    completata = models.BooleanField(default=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='attivita_assegnate',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Attività'
        verbose_name_plural = 'Attività'
        ordering = ['-data']

    def __str__(self):
        return self.titolo

    def get_absolute_url(self):
        return reverse('attivita:detail', args=[self.pk])

    @property
    def icon(self):
        return TIPO_ICON.get(self.tipo, 'clock')

    @property
    def color(self):
        return TIPO_COLOR.get(self.tipo, 'var(--ink-3)')
