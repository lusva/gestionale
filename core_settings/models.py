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
