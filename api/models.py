import hashlib
import json
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models

from tenancy.models import TenantModel
from django.utils import timezone

# Ogni quanto al massimo si riscrive ``last_used_at``: una scrittura a ogni
# chiamata API è una scrittura sul DB per ogni lettura.
USO_AGGIORNA_OGNI = timedelta(minutes=5)
LUNGHEZZA_PREFISSO = 12          # 'aigis_' + 6 caratteri: riconoscibile, non segreto


def _make_token() -> str:
    return 'aigis_' + secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    """SHA-256 esadecimale del token in chiaro: è quello che sta nel DB."""
    return hashlib.sha256((raw or '').encode('utf-8')).hexdigest()


class ApiToken(TenantModel):
    """Token API (Bearer) associato a un utente.

    Nel DB resta solo l'hash SHA-256 del token (``token_hash``) e un
    prefisso non segreto per riconoscerlo. Il valore in chiaro esiste solo
    sull'istanza appena creata (``token_in_chiaro``): la view lo mostra una
    volta e poi non è più recuperabile.
    """
    name = models.CharField(max_length=80, help_text='Etichetta per riconoscere il token')
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    prefisso = models.CharField(max_length=LUNGHEZZA_PREFISSO, blank=True, editable=False,
                                help_text='Inizio del token, per riconoscerlo (non segreto).')
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='api_tokens',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)
    scrittura = models.BooleanField(
        default=False,
        help_text="Se attivo il token può creare documenti tramite /api/v2/ "
                  "(sempre nei limiti del ruolo dell'utente).",
    )

    # Valorizzato solo sull'istanza che ha generato il token (mai letto dal DB).
    token_in_chiaro: str | None = None

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['-created_at']
        verbose_name = 'API token'
        verbose_name_plural = 'API token'

    def __str__(self):
        return f'{self.name} ({self.user})'

    def save(self, *args, **kwargs):
        if not self.token_hash:
            raw = _make_token()
            self.token_in_chiaro = raw
            self.token_hash = hash_token(raw)
            self.prefisso = raw[:LUNGHEZZA_PREFISSO]
        super().save(*args, **kwargs)

    @classmethod
    def da_token(cls, raw: str | None):
        """Il token attivo corrispondente al valore in chiaro, o None."""
        if not raw:
            return None
        # Prima dell'autenticazione non conosciamo ancora l'azienda. Questo
        # lookup globale è riservato al Bearer; le query dell'endpoint sono scoped.
        return (cls.all_objects.select_related('user__profile', 'azienda')
                .filter(token_hash=hash_token(raw), revoked=False).first())

    def segna_uso(self):
        """Aggiorna ``last_used_at``, ma non più di una volta ogni pochi minuti."""
        adesso = timezone.now()
        if self.last_used_at is None or adesso - self.last_used_at >= USO_AGGIORNA_OGNI:
            ApiToken.objects.filter(pk=self.pk).update(last_used_at=adesso)
            self.last_used_at = adesso


class RichiestaIdempotente(TenantModel):
    """Risposta già data a una scrittura con header ``Idempotency-Key``.

    Un doppio clic o un ritentativo di rete con la stessa chiave ricevono la
    stessa risposta invece di creare un secondo documento. La riga nasce
    nella stessa transazione del documento: se il salvataggio fallisce non
    resta nulla.
    """
    token = models.ForeignKey(ApiToken, on_delete=models.CASCADE, related_name='richieste')
    chiave = models.CharField(max_length=80)
    percorso = models.CharField(max_length=200)
    # SHA-256 del corpo della richiesta: la stessa chiave con dati diversi è un errore
    hash_corpo = models.CharField(max_length=64, blank=True, default='')
    stato_http = models.PositiveSmallIntegerField()
    risposta = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        constraints = [
            models.UniqueConstraint(fields=['azienda', 'token', 'chiave'], name='richiesta_idempotente_unica'),
        ]
        verbose_name = 'Richiesta idempotente'
        verbose_name_plural = 'Richieste idempotenti'

    def __str__(self):
        return f'{self.chiave} → {self.percorso} ({self.stato_http})'

    @classmethod
    def pulisci(cls, **filtri) -> int:
        """Cancella le risposte più vecchie di ``IDEMPOTENZA_ORE`` (24 h):
        oltre quel tempo la chiave torna libera."""
        limite = timezone.now() - timedelta(hours=getattr(settings, 'IDEMPOTENZA_ORE', 24))
        return cls.objects.filter(created_at__lt=limite, **filtri).delete()[0]


def hash_corpo(dati) -> str:
    """Impronta stabile del corpo di una richiesta (bytes o oggetto JSON)."""
    if not isinstance(dati, (bytes, bytearray)):
        dati = json.dumps(dati, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')
    return hashlib.sha256(dati).hexdigest()


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


class Webhook(TenantModel):
    """Endpoint esterno che riceve eventi da Gestionale CRM via HTTP POST JSON.

    Il delivery parte dopo il commit, in un thread in background (nessuna
    coda né retry: per traffico serio, Celery / RQ). Secret HMAC-SHA256
    invia `X-CRM-Signature`.
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
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def accetta(self, evento: str) -> bool:
        if not self.eventi:
            return True
        return evento in self.eventi
