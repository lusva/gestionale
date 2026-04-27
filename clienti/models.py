from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.urls import reverse


class TipoCliente(models.TextChoices):
    AZIENDA = 'azienda', 'Azienda'
    PRIVATO = 'privato', 'Privato'
    ENTE = 'ente', 'Ente pubblico'


class StatoCliente(models.TextChoices):
    ATTIVO = 'attivo', 'Attivo'
    PROSPECT = 'prospect', 'Prospect'
    ATTESA = 'attesa', 'In attesa'
    INATTIVO = 'inattivo', 'Inattivo'


class Tag(models.Model):
    nome = models.CharField(max_length=50, unique=True)
    colore = models.CharField(
        max_length=20, default='info',
        choices=[
            ('info', 'Info'),
            ('success', 'Success'),
            ('warn', 'Warn'),
            ('danger', 'Danger'),
            ('neutral', 'Neutro'),
        ],
    )

    class Meta:
        ordering = ['nome']

    def __str__(self):
        return self.nome


class Settore(models.Model):
    nome = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)

    class Meta:
        ordering = ['nome']
        verbose_name = 'Settore'
        verbose_name_plural = 'Settori'

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            self.slug = slugify(self.nome)[:120]
        super().save(*args, **kwargs)


class Cliente(models.Model):
    ragione_sociale = models.CharField(max_length=200)
    tipo = models.CharField(
        max_length=10, choices=TipoCliente.choices, default=TipoCliente.AZIENDA,
    )
    settore = models.ForeignKey(
        Settore,
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name='clienti',
    )

    partita_iva = models.CharField(max_length=16, unique=True, db_index=True)
    codice_fiscale = models.CharField(max_length=16, blank=True)
    codice_sdi = models.CharField(max_length=7, blank=True)
    pec = models.EmailField(blank=True)

    indirizzo = models.CharField(max_length=200, blank=True)
    cap = models.CharField(max_length=5, blank=True)
    citta = models.CharField(max_length=100, blank=True)
    provincia = models.CharField(max_length=2, blank=True)
    nazione = models.CharField(max_length=2, default='IT')

    stato = models.CharField(
        max_length=10, choices=StatoCliente.choices, default=StatoCliente.PROSPECT,
    )
    account_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='clienti_gestiti',
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name='clienti')

    # Commercial / info
    numero_dipendenti = models.CharField(max_length=30, blank=True)
    fatturato_annuo_stimato = models.CharField(max_length=40, blank=True)
    note = models.TextField(blank=True)

    # Dati fiscali (collegati alla app anagrafiche). Opzionali: un cliente CRM
    # in stato prospect può non averli ancora compilati. Diventano obbligatori
    # solo al momento della fatturazione.
    forme_pagamento = models.ForeignKey(
        'anagrafiche.FormePagamento',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='clienti',
    )
    posizione_iva = models.ForeignKey(
        'anagrafiche.PosizioneIva',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='clienti',
    )
    referente = models.ForeignKey(
        'anagrafiche.Referente',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='clienti',
    )
    profilo_fiscale = models.ForeignKey(
        'anagrafiche.ProfiloFiscale',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='clienti',
    )
    fatturazione_elettronica = models.OneToOneField(
        'anagrafiche.FatturazioneElettronica',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='cliente',
    )

    cliente_dal = models.DateField(null=True, blank=True)
    ultimo_contatto = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['ragione_sociale']
        indexes = [
            models.Index(fields=['stato']),
            models.Index(fields=['settore']),
            models.Index(fields=['citta']),
        ]

    def __str__(self):
        return self.ragione_sociale

    def get_absolute_url(self):
        return reverse('clienti:detail', args=[self.pk])

    @property
    def iniziali(self):
        parts = [p for p in self.ragione_sociale.split() if p]
        return ''.join(p[0] for p in parts[:2]).upper() or '??'

    @property
    def is_azienda(self):
        return self.tipo == TipoCliente.AZIENDA

    @property
    def fatturato_totale(self):
        total = self.opportunita.filter(stadio='chiusa_win').aggregate(
            s=Sum('valore'),
        )['s']
        return total or 0

    @property
    def opportunita_aperte(self):
        return self.opportunita.exclude(
            stadio__in=['chiusa_win', 'chiusa_lost'],
        )

    @property
    def tasso_chiusura(self):
        vinte = self.opportunita.filter(stadio='chiusa_win').count()
        perse = self.opportunita.filter(stadio='chiusa_lost').count()
        total = vinte + perse
        if not total:
            return 0
        return round((vinte / total) * 100)


class Contatto(models.Model):
    cliente = models.ForeignKey(
        Cliente, on_delete=models.CASCADE, related_name='contatti',
    )
    nome = models.CharField(max_length=100)
    cognome = models.CharField(max_length=100)
    ruolo = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    primary = models.BooleanField(default=False, verbose_name='Contatto principale')
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-primary', 'cognome', 'nome']

    def __str__(self):
        return f'{self.nome} {self.cognome}'.strip()

    @property
    def nome_completo(self):
        return f'{self.nome} {self.cognome}'.strip()

    @property
    def iniziali(self):
        iniziali = ''
        if self.nome:
            iniziali += self.nome[0]
        if self.cognome:
            iniziali += self.cognome[0]
        return iniziali.upper() or '?'

    def save(self, *args, **kwargs):
        if self.primary:
            Contatto.objects.filter(
                cliente=self.cliente, primary=True,
            ).exclude(pk=self.pk).update(primary=False)
        super().save(*args, **kwargs)
