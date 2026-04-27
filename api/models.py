import secrets

from django.conf import settings
from django.db import models
from django.utils.crypto import constant_time_compare


def _make_token() -> str:
    return 'aigis_' + secrets.token_urlsafe(32)


class ApiToken(models.Model):
    """Token API (Bearer) associato a un utente.

    Il token viene generato al create e restituito in chiaro *una volta sola*
    dalla view. Dopo il save, in DB rimane lo stesso valore (semplicità);
    per un livello più alto di sicurezza si potrebbe salvarne solo l'hash.
    """
    name = models.CharField(max_length=80, help_text='Etichetta per riconoscere il token')
    token = models.CharField(max_length=80, unique=True, default=_make_token, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='api_tokens',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'API token'
        verbose_name_plural = 'API token'

    def __str__(self):
        return f'{self.name} ({self.user})'

    def matches(self, raw_token: str) -> bool:
        return not self.revoked and constant_time_compare(self.token, raw_token or '')


class WebhookEvento(models.TextChoices):
    CLIENTE_CREATO = 'cliente.creato', 'Cliente creato'
    CLIENTE_MODIFICATO = 'cliente.modificato', 'Cliente modificato'
    CLIENTE_ELIMINATO = 'cliente.eliminato', 'Cliente eliminato'
    OPP_CREATA = 'opportunita.creata', 'Opportunità creata'
    OPP_MODIFICATA = 'opportunita.modificata', 'Opportunità modificata'
    OPP_CHIUSA_WIN = 'opportunita.chiusa_win', 'Opportunità vinta'
    OPP_CHIUSA_LOST = 'opportunita.chiusa_lost', 'Opportunità persa'
    # Documenti
    FATTURA_CREATA = 'fattura.creata', 'Fattura creata'
    FATTURA_MODIFICATA = 'fattura.modificata', 'Fattura modificata'
    FATTURA_PAGATA = 'fattura.pagata', 'Fattura pagata'
    OFFERTA_CREATA = 'offerta.creata', 'Offerta creata'
    ORDINE_CREATO = 'ordine.creato', 'Ordine creato'
    DDT_CREATO = 'ddt.creato', 'DDT creato'
    NOTA_CREDITO_CREATA = 'nota_credito.creata', 'Nota di credito creata'
    FATTURA_ACQUISTO_IMPORTATA = 'fattura_acquisto.importata', 'Fattura acquisto importata'


class Webhook(models.Model):
    """Endpoint esterno che riceve eventi da Gestionale CRM via HTTP POST JSON.

    Il delivery è sincrono (nessuna coda). In produzione serio, sostituire
    con Celery / RQ. Secret HMAC-SHA256 invia `X-CRM-Signature`.
    """
    name = models.CharField(max_length=80)
    url = models.URLField(max_length=300)
    eventi = models.JSONField(
        default=list, help_text='Lista di eventi (chiavi WebhookEvento). Vuoto = tutti',
    )
    secret = models.CharField(
        max_length=64, blank=True,
        help_text='Se impostato, il body viene firmato (HMAC-SHA256) → header X-CRM-Signature',
    )
    attivo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Stato ultima consegna
    ultimo_tentativo_at = models.DateTimeField(null=True, blank=True)
    ultimo_status = models.PositiveIntegerField(null=True, blank=True)
    ultimo_errore = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def accetta(self, evento: str) -> bool:
        if not self.eventi:
            return True
        return evento in self.eventi
