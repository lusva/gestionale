from django.db import models


class Organizzazione(models.Model):
    nome = models.CharField(max_length=200, default='Studio Rossi SRL')
    logo = models.ImageField(upload_to='org/', blank=True, null=True)
    piano = models.CharField(max_length=30, default='Pro')
    posti_totali = models.PositiveIntegerField(default=8)
    fuso_orario = models.CharField(max_length=50, default='Europe/Rome')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Organizzazione'
        verbose_name_plural = 'Organizzazione'

    def __str__(self):
        return self.nome

    @classmethod
    def current(cls):
        org = cls.objects.first()
        if org is None:
            org = cls.objects.create()
        return org


class EsecuzioneComando(models.Model):
    """Ultima esecuzione dei comandi periodici lanciati dai timer systemd
    (``sdi_pull``…). Serve a mostrare nelle impostazioni se girano davvero e,
    con ``in_corso_dal``, a non farne partire due insieme (il timer e il
    pulsante «Controlla ora»). Vedi ``core_settings.esecuzioni``."""

    nome = models.CharField(max_length=60, unique=True)
    in_corso_dal = models.DateTimeField(null=True, blank=True)
    ultimo_avvio = models.DateTimeField(null=True, blank=True)
    ultima_fine = models.DateTimeField(null=True, blank=True)
    esito = models.CharField(max_length=10, blank=True, default='')  # ok | errore
    riepilogo = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'Esecuzione comando'
        verbose_name_plural = 'Esecuzioni comandi'

    def __str__(self):
        return self.nome
