from django.conf import settings
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


class Ruolo(models.TextChoices):
    AMMINISTRATORE = 'amministratore', 'Amministratore'
    ACCOUNT_MANAGER = 'account_manager', 'Account manager'
    VISUALIZZATORE = 'visualizzatore', 'Visualizzatore'


class StatoUtente(models.TextChoices):
    ATTIVO = 'attivo', 'Attivo'
    SOSPESO = 'sospeso', 'Sospeso'
    INVITATO = 'invitato', 'Invitato'


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    ruolo = models.CharField(
        max_length=20, choices=Ruolo.choices, default=Ruolo.ACCOUNT_MANAGER,
    )
    stato = models.CharField(
        max_length=20, choices=StatoUtente.choices, default=StatoUtente.ATTIVO,
    )
    telefono = models.CharField(max_length=30, blank=True)
    iniziali = models.CharField(max_length=4, blank=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    ultimo_accesso = models.DateTimeField(null=True, blank=True)

    tema = models.CharField(
        max_length=10,
        choices=[('light', 'Chiaro'), ('dark', 'Scuro')],
        default='light',
    )
    densita = models.CharField(
        max_length=10,
        choices=[('compact', 'Compatta'), ('normal', 'Normale'), ('comfy', 'Comoda')],
        default='normal',
    )

    class Meta:
        verbose_name = 'Profilo utente'
        verbose_name_plural = 'Profili utenti'

    def __str__(self):
        return self.user.get_full_name() or self.user.username

    def save(self, *args, **kwargs):
        if not self.iniziali:
            name = self.user.get_full_name() or self.user.username
            parts = [p for p in name.split() if p]
            self.iniziali = ''.join(p[0] for p in parts[:2]).upper() or self.user.username[:2].upper()
        super().save(*args, **kwargs)

    @property
    def nome_completo(self):
        return self.user.get_full_name() or self.user.username

    @property
    def ruolo_label(self):
        return self.get_ruolo_display()


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)
