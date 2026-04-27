"""
Modelli del ciclo attivo (Offerta → Ordine → DDT → Fattura → Nota Credito) e
del ciclo passivo (DDT Fornitore, Fattura Acquisto + scadenze + pagamenti).

Convenzioni:
- ogni testata è auto-numerata progressivamente per anno solare, con
  ``select_for_update`` per evitare race condition su MySQL/PostgreSQL
  (in SQLite il lock è inattivo ma il save() resta atomico tramite
  ``transaction.atomic``).
- l'imponibile della testata è derivato (ricalcolato dai signal post_save
  di ogni RigaDocumento e dalla testata stessa al primo save).
- l'integrazione con il modulo Magazzino è demandata alla relativa app:
  qui esponiamo l'helper ``_modulo_magazzino_attivo`` come hook che
  l'app magazzino userà tramite signal in Fase 5.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils.timezone import now


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _modulo_magazzino_attivo():
    """True se il modulo Magazzino è attivo sull'AnagraficaAzienda.

    Tabella mono-record: se non esiste ancora un record, consideriamo il
    modulo attivo (default safe). Qualsiasi errore di accesso DB → True
    per non rompere il save dei documenti durante setup iniziale.
    """
    try:
        from anagrafiche.models import AnagraficaAzienda
        azienda = AnagraficaAzienda.objects.first()
    except Exception:
        return True
    if azienda is None:
        return True
    return bool(azienda.modulo_magazzino_attivo)


def _next_numero_per_anno(model, anno, sezionale=None):
    """Numero successivo (per anno, sezionale) per il modello dato.

    Se ``sezionale`` è None il modello non ha l'attributo (Offerta, Ordine,
    DDT, DDT fornitore): si usa la numerazione globale per anno. Se è una
    stringa (anche vuota) si filtra anche su ``sezionale=value`` mantenendo
    progressivi separati per ogni sezionale (es. fattura immediata vs
    differita vs autofattura).
    """
    qs = model.objects.filter(anno=anno)
    if sezionale is not None:
        qs = qs.filter(sezionale=sezionale)
    last = qs.select_for_update().order_by('numero').last()
    return (last.numero + 1) if last else 1


def _recalc_imponibile(testata, related_name='righe'):
    """Ricalcola imponibile sommando ``importo_unitario * quantita`` delle righe."""
    imponibile = Decimal('0.00')
    for row in getattr(testata, related_name).all():
        imponibile += Decimal(str(row.importo_unitario)) * Decimal(str(row.quantita))
    type(testata).objects.filter(pk=testata.pk).update(imponibile=imponibile)


# ---------------------------------------------------------------------------
# Testata astratta comune
# ---------------------------------------------------------------------------


class TestataDocumento(models.Model):
    numero = models.PositiveIntegerField(editable=False)
    anno = models.PositiveIntegerField(editable=False)
    data_documento = models.DateField(default=now, null=True, blank=True)
    data_registrazione = models.DateField(default=now, null=True, blank=True)
    cliente = models.ForeignKey(
        'clienti.Cliente',
        on_delete=models.PROTECT,
        blank=True, null=True,
        related_name='+',
    )
    fornitore = models.ForeignKey(
        'anagrafiche.Fornitore',
        on_delete=models.PROTECT,
        blank=True, null=True,
        related_name='+',
    )
    spese_imballo = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
    )
    spese_trasporto = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
    )
    sconto = models.DecimalField(
        max_digits=4, decimal_places=2, blank=True, null=True,
    )
    sconto_incondizionato = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
    )
    note = models.TextField(blank=True, null=True)
    forme_pagamento = models.ForeignKey(
        'anagrafiche.FormePagamento',
        on_delete=models.SET_NULL, blank=True, null=True,
    )
    conto_corrente = models.ForeignKey(
        'anagrafiche.ContoCorrente',
        on_delete=models.SET_NULL, blank=True, null=True,
    )
    imponibile = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True, default=0,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if not self.cliente and not self.fornitore:
            raise ValidationError(
                'Devi specificare almeno un Cliente o un Fornitore.'
            )

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# Ciclo attivo
# ---------------------------------------------------------------------------


class TestataOfferta(TestataDocumento):
    class StatoOfferta(models.TextChoices):
        ATTESA = '0', 'In attesa'
        CONFERMATA = '1', 'Confermata'
        ANNULLATA = '2', 'Annullata'

    scadenza = models.DateField(null=True, blank=True)
    stato = models.CharField(
        max_length=10,
        choices=StatoOfferta.choices,
        default=StatoOfferta.ATTESA,
    )
    ordine_collegato = models.ManyToManyField(
        'TestataOrdine', blank=True, related_name='offerte_origine',
    )

    def save(self, *args, **kwargs):
        if self._state.adding:
            anno = now().year
            with transaction.atomic():
                self.numero = _next_numero_per_anno(TestataOfferta, anno)
                self.anno = anno
        super().save(*args, **kwargs)

    def __str__(self):
        return f'Offerta {self.numero}/{self.anno} - {self.cliente or self.fornitore or ""}'

    class Meta:
        ordering = ['-anno', '-numero']
        verbose_name = 'Offerta'
        verbose_name_plural = 'Offerte'


class TestataOrdine(TestataDocumento):
    class StatoOrdine(models.TextChoices):
        ATTESA = '0', 'In attesa'
        CONFERMATA = '1', 'Confermata'
        ANNULLATA = '2', 'Annullata'

    stato = models.CharField(
        max_length=10,
        choices=StatoOrdine.choices,
        default=StatoOrdine.ATTESA,
    )
    ddt_collegato = models.ManyToManyField(
        'TestataDdt', blank=True, related_name='ordini_origine',
    )
    fattura_collegata = models.ManyToManyField(
        'TestataFattura', blank=True, related_name='ordini_origine',
    )

    def save(self, *args, **kwargs):
        if self._state.adding:
            anno = now().year
            with transaction.atomic():
                self.numero = _next_numero_per_anno(TestataOrdine, anno)
                self.anno = anno
        super().save(*args, **kwargs)

    def __str__(self):
        return f'Ordine {self.numero}/{self.anno} - {self.cliente or self.fornitore or ""}'

    class Meta:
        ordering = ['-anno', '-numero']
        verbose_name = 'Ordine'
        verbose_name_plural = 'Ordini'


class TestataDdt(TestataDocumento):
    class StatoDdt(models.TextChoices):
        CONFERMATO = '0', 'Confermato'
        ANNULLATO = '1', 'Annullato'

    numero_palette = models.PositiveIntegerField(default=0)
    peso_netto = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    peso_lordo = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cura = models.CharField(max_length=50, blank=True, default='')
    causale_trasporto = models.CharField(max_length=50, blank=True, default='')
    porto = models.CharField(max_length=50, blank=True, default='')
    aspetto_beni = models.CharField(max_length=50, blank=True, default='')
    vettore = models.CharField(max_length=50, blank=True, default='')
    data_ora_ritiro = models.DateTimeField(null=True, blank=True, default=now)
    destinazione_merce = models.ForeignKey(
        'anagrafiche.Indirizzo',
        on_delete=models.SET_NULL,
        blank=True, null=True,
        related_name='+',
    )
    stato = models.CharField(
        max_length=10,
        choices=StatoDdt.choices,
        default=StatoDdt.CONFERMATO,
    )

    def save(self, *args, **kwargs):
        if self._state.adding:
            anno = now().year
            with transaction.atomic():
                self.numero = _next_numero_per_anno(TestataDdt, anno)
                self.anno = anno
        super().save(*args, **kwargs)

    def __str__(self):
        return f'DDT {self.numero}/{self.anno} - {self.cliente or ""}'

    class Meta:
        ordering = ['-anno', '-numero']
        verbose_name = 'DDT'
        verbose_name_plural = 'DDT'


class TestataFattura(TestataDocumento):
    class TipoDocumento(models.TextChoices):
        TD01 = 'TD01', 'TD01 - Fattura'
        TD02 = 'TD02', 'TD02 - Acconto/anticipo su fattura'
        TD03 = 'TD03', 'TD03 - Acconto/anticipo su parcella'
        TD05 = 'TD05', 'TD05 - Nota di debito'
        TD06 = 'TD06', 'TD06 - Parcella'
        TD16 = 'TD16', 'TD16 - Integrazione reverse charge interno'
        TD17 = 'TD17', 'TD17 - Integrazione/autofattura servizi estero'
        TD18 = 'TD18', 'TD18 - Integrazione acquisto beni intra-UE'
        TD19 = 'TD19', 'TD19 - Integrazione/autofattura acquisto beni'
        TD20 = 'TD20', 'TD20 - Autofattura denuncia'
        TD21 = 'TD21', 'TD21 - Autofattura per splafonamento'
        TD22 = 'TD22', 'TD22 - Estrazione beni Deposito IVA'
        TD23 = 'TD23', 'TD23 - Estrazione beni Deposito IVA con vers. IVA'
        TD24 = 'TD24', 'TD24 - Fattura differita art.21 c.4 lett.a'
        TD25 = 'TD25', 'TD25 - Fattura differita art.21 c.4 lett.b (triangolari interne)'
        TD26 = 'TD26', 'TD26 - Cessione beni ammortizzabili'
        TD27 = 'TD27', 'TD27 - Autoconsumo o cessioni gratuite'

    tipo_documento = models.CharField(
        max_length=10,
        choices=TipoDocumento.choices,
        default=TipoDocumento.TD24,
    )
    sezionale = models.CharField(
        max_length=10, blank=True, default='',
        help_text="Sezionale di numerazione (es. 'A' per immediate, 'B' per "
                  "differite). Lasciare vuoto per progressivo unico.",
    )
    nota_credito_collegata = models.ManyToManyField(
        'TestataNotaCredito', blank=True, related_name='fatture_origine',
    )
    pagata = models.BooleanField(default=False)
    data_pagamento = models.DateField(null=True, blank=True)

    # Stato SDI: ciclo di vita di una fattura inviata al Sistema di Interscambio.
    # Aggiornato manualmente dall'invio (``inviata``) e automaticamente dalla
    # consumazione delle notifiche IMAP (``ricevuta_sdi``, ``accettata``,
    # ``scartata``, ``mancata_consegna``).
    class SdiStato(models.TextChoices):
        NON_INVIATA = 'non_inviata', 'Non inviata'
        INVIATA = 'inviata', 'Inviata'
        RICEVUTA_SDI = 'ricevuta_sdi', 'Ricevuta dal SDI'
        ACCETTATA = 'accettata', 'Accettata (consegnata al destinatario)'
        SCARTATA = 'scartata', 'Scartata'
        MANCATA_CONSEGNA = 'mancata_consegna', 'Mancata consegna (in giacenza)'

    sdi_stato = models.CharField(
        max_length=20,
        choices=SdiStato.choices,
        default=SdiStato.NON_INVIATA,
    )
    sdi_data_invio = models.DateTimeField(null=True, blank=True)
    sdi_id_trasmissione = models.CharField(
        max_length=80, blank=True, default='',
        help_text="ID trasmissione assegnato dal SDI (ricevuta RC).",
    )
    sdi_ultimo_messaggio = models.TextField(blank=True, default='')

    def save(self, *args, **kwargs):
        if self._state.adding:
            anno = now().year
            with transaction.atomic():
                self.numero = _next_numero_per_anno(
                    TestataFattura, anno, sezionale=self.sezionale or '',
                )
                self.anno = anno
        super().save(*args, **kwargs)

    def __str__(self):
        sez = f'{self.sezionale}/' if self.sezionale else ''
        return f'Fattura {sez}{self.numero}/{self.anno} - {self.cliente or ""}'

    class Meta:
        ordering = ['-anno', '-numero']
        verbose_name = 'Fattura'
        verbose_name_plural = 'Fatture'


class TestataNotaCredito(TestataDocumento):
    class TipoDocumento(models.TextChoices):
        TD04 = 'TD04', 'TD04 - Nota di credito'
        TD08 = 'TD08', 'TD08 - Nota di credito semplificata'

    tipo_documento = models.CharField(
        max_length=10,
        choices=TipoDocumento.choices,
        default=TipoDocumento.TD04,
    )
    sezionale = models.CharField(
        max_length=10, blank=True, default='',
        help_text="Sezionale di numerazione (separato dalle fatture).",
    )

    def save(self, *args, **kwargs):
        if self._state.adding:
            anno = now().year
            with transaction.atomic():
                self.numero = _next_numero_per_anno(
                    TestataNotaCredito, anno, sezionale=self.sezionale or '',
                )
                self.anno = anno
        super().save(*args, **kwargs)

    def __str__(self):
        sez = f'{self.sezionale}/' if self.sezionale else ''
        return f'Nota Credito {sez}{self.numero}/{self.anno} - {self.cliente or ""}'

    class Meta:
        ordering = ['-anno', '-numero']
        verbose_name = 'Nota di credito'
        verbose_name_plural = 'Note di credito'


# ---------------------------------------------------------------------------
# Ciclo passivo (DDT Fornitore + Fattura Acquisto)
# ---------------------------------------------------------------------------


class TestataDdtFornitore(TestataDocumento):
    numero_palette = models.PositiveIntegerField(default=0)
    peso_netto = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    peso_lordo = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    vettore = models.CharField(max_length=50, blank=True, default='')

    def save(self, *args, **kwargs):
        if self._state.adding:
            anno = now().year
            with transaction.atomic():
                self.numero = _next_numero_per_anno(TestataDdtFornitore, anno)
                self.anno = anno
        super().save(*args, **kwargs)

    def __str__(self):
        return f'DDT Fornitore {self.numero}/{self.anno} - {self.fornitore or ""}'

    class Meta:
        ordering = ['-anno', '-numero']
        verbose_name = 'DDT fornitore'
        verbose_name_plural = 'DDT fornitore'


class TestataFatturaAcquisto(TestataDocumento):
    class TipoDocumento(models.TextChoices):
        TD01 = 'TD01', 'TD01 - Fattura'
        TD02 = 'TD02', 'TD02 - Acconto/anticipo su fattura'
        TD03 = 'TD03', 'TD03 - Acconto/anticipo su parcella'
        TD04 = 'TD04', 'TD04 - Nota di credito'
        TD05 = 'TD05', 'TD05 - Nota di debito'
        TD06 = 'TD06', 'TD06 - Parcella'
        TD16 = 'TD16', 'TD16 - Integrazione reverse charge interno'
        TD17 = 'TD17', 'TD17 - Integrazione/autofattura servizi estero'
        TD18 = 'TD18', 'TD18 - Integrazione acquisto beni intra-UE'
        TD19 = 'TD19', 'TD19 - Integrazione/autofattura acquisto beni'
        TD20 = 'TD20', 'TD20 - Autofattura denuncia'
        TD21 = 'TD21', 'TD21 - Autofattura per splafonamento'
        TD22 = 'TD22', 'TD22 - Estrazione beni Deposito IVA'
        TD23 = 'TD23', 'TD23 - Estrazione beni Deposito IVA con vers. IVA'
        TD24 = 'TD24', 'TD24 - Fattura differita art.21 c.4 lett.a'
        TD25 = 'TD25', 'TD25 - Fattura differita art.21 c.4 lett.b'
        TD26 = 'TD26', 'TD26 - Cessione beni ammortizzabili'
        TD27 = 'TD27', 'TD27 - Autoconsumo o cessioni gratuite'

    class StatoFattura(models.TextChoices):
        BOZZA = 'bozza', 'Bozza'
        CONFERMATA = 'confermata', 'Confermata'
        ANNULLATA = 'annullata', 'Annullata'

    numero_fornitore = models.CharField(max_length=50, blank=True, null=True)
    data_fornitore = models.DateField(null=True, blank=True)
    tipo_documento = models.CharField(
        max_length=10,
        choices=TipoDocumento.choices,
        default=TipoDocumento.TD01,
    )
    stato = models.CharField(
        max_length=20,
        choices=StatoFattura.choices,
        default=StatoFattura.BOZZA,
    )
    imposta = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True, default=0,
    )
    totale_documento = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True, default=0,
    )
    pagata = models.BooleanField(default=False)
    data_pagamento = models.DateField(null=True, blank=True)
    xml_originale = models.FileField(
        upload_to='fatture_acquisto_xml/', blank=True, null=True,
    )

    def save(self, *args, **kwargs):
        if self._state.adding:
            anno = now().year
            with transaction.atomic():
                self.numero = _next_numero_per_anno(TestataFatturaAcquisto, anno)
                self.anno = anno
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f'Fattura acquisto {self.numero}/{self.anno} - '
            f'{self.fornitore or ""} {self.numero_fornitore or ""}'
        )

    class Meta:
        ordering = ['-anno', '-numero']
        verbose_name = 'Fattura acquisto'
        verbose_name_plural = 'Fatture acquisto'
        constraints = [
            models.UniqueConstraint(
                fields=['fornitore', 'numero_fornitore', 'data_fornitore'],
                name='documenti_fattura_acq_unique_per_fornitore',
                condition=models.Q(numero_fornitore__isnull=False),
            ),
        ]


# ---------------------------------------------------------------------------
# Righe documento (ciclo attivo + DDT fornitore)
# ---------------------------------------------------------------------------


class RigaDocumento(models.Model):
    class UM(models.TextChoices):
        PZ = 'PZ', 'PZ'
        MQ = 'MQ', 'MQ'
        ML = 'ML', 'ML'
        KG = 'KG', 'KG'
        LT = 'LT', 'LT'
        ORE = 'ORE', 'ORE'
        N = 'N', 'N'

    numero_riga = models.PositiveIntegerField(default=0, blank=True, null=True)

    # Esattamente UNA tra le seguenti FK è valorizzata per riga.
    testata_offerta = models.ForeignKey(
        TestataOfferta, on_delete=models.CASCADE,
        blank=True, null=True, related_name='righe',
    )
    testata_ordine = models.ForeignKey(
        TestataOrdine, on_delete=models.CASCADE,
        blank=True, null=True, related_name='righe',
    )
    testata_ddt = models.ForeignKey(
        TestataDdt, on_delete=models.CASCADE,
        blank=True, null=True, related_name='righe',
    )
    testata_fattura = models.ForeignKey(
        TestataFattura, on_delete=models.CASCADE,
        blank=True, null=True, related_name='righe',
    )
    testata_nota_credito = models.ForeignKey(
        TestataNotaCredito, on_delete=models.CASCADE,
        blank=True, null=True, related_name='righe',
    )
    testata_ddt_fornitore = models.ForeignKey(
        TestataDdtFornitore, on_delete=models.CASCADE,
        blank=True, null=True, related_name='righe',
    )

    articolo = models.ForeignKey(
        'anagrafiche.Articolo',
        on_delete=models.SET_NULL,
        blank=True, null=True,
    )
    descrizione_libera = models.TextField(max_length=1000, blank=True, null=True)
    importo_unitario = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0'),
    )
    quantita = models.DecimalField(max_digits=10, decimal_places=2)
    iva = models.ForeignKey(
        'anagrafiche.PosizioneIva',
        on_delete=models.SET_NULL,
        blank=True, null=True,
    )
    um = models.CharField(max_length=10, blank=True, null=True, choices=UM.choices)
    colli = models.PositiveIntegerField(blank=True, null=True, default=0)
    prezzo_acquisto = models.DecimalField(
        max_digits=10, decimal_places=2,
        blank=True, null=True, default=Decimal('0'),
    )

    @property
    def imponibile(self):
        return Decimal(str(self.importo_unitario)) * Decimal(str(self.quantita))

    @property
    def imposta(self):
        if self.iva is None:
            return Decimal('0')
        return self.imponibile * self.iva.aliquota / Decimal('100')

    @property
    def totale(self):
        return self.imponibile + self.imposta

    def __str__(self):
        return f'{self.numero_riga or self.pk} {self.articolo or self.descrizione_libera or ""} x {self.quantita}'

    def clean(self):
        # Esattamente una testata deve essere valorizzata
        testate = [
            self.testata_offerta_id, self.testata_ordine_id,
            self.testata_ddt_id, self.testata_fattura_id,
            self.testata_nota_credito_id, self.testata_ddt_fornitore_id,
        ]
        valorizzate = [t for t in testate if t]
        if len(valorizzate) != 1:
            raise ValidationError(
                'Una riga deve appartenere esattamente a una testata.'
            )

    class Meta:
        ordering = ['numero_riga', 'id']
        verbose_name = 'Riga documento'
        verbose_name_plural = 'Righe documento'


# ---------------------------------------------------------------------------
# Righe / scadenze / pagamenti fattura acquisto
# ---------------------------------------------------------------------------


class RigaFatturaAcquisto(models.Model):
    class UM(models.TextChoices):
        PZ = 'PZ', 'PZ'
        MQ = 'MQ', 'MQ'
        ML = 'ML', 'ML'
        KG = 'KG', 'KG'
        LT = 'LT', 'LT'
        H = 'H', 'H'
        N = 'N', 'N'

    testata = models.ForeignKey(
        TestataFatturaAcquisto, on_delete=models.CASCADE, related_name='righe',
    )
    numero_riga = models.PositiveIntegerField(default=0, blank=True, null=True)
    articolo = models.ForeignKey(
        'anagrafiche.Articolo', on_delete=models.SET_NULL, blank=True, null=True,
    )
    descrizione = models.TextField(max_length=1000, blank=True, null=True)
    quantita = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('1'),
    )
    um = models.CharField(max_length=10, blank=True, null=True, choices=UM.choices)
    prezzo_unitario = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal('0'),
    )
    sconto_percentuale = models.DecimalField(
        max_digits=5, decimal_places=2, blank=True, null=True,
    )
    aliquota_iva = models.ForeignKey(
        'anagrafiche.PosizioneIva',
        on_delete=models.SET_NULL, blank=True, null=True,
    )
    categoria_costo = models.ForeignKey(
        'anagrafiche.CategoriaCosto',
        on_delete=models.SET_NULL, blank=True, null=True,
    )

    @property
    def imponibile(self):
        base = self.prezzo_unitario * self.quantita
        if self.sconto_percentuale:
            base = base * (Decimal('1') - self.sconto_percentuale / Decimal('100'))
        return base

    @property
    def imposta(self):
        if self.aliquota_iva is None:
            return Decimal('0')
        return self.imponibile * self.aliquota_iva.aliquota / Decimal('100')

    @property
    def totale(self):
        return self.imponibile + self.imposta

    def __str__(self):
        return f'{self.testata_id} - {(self.descrizione or "")[:40]}'

    class Meta:
        ordering = ['numero_riga', 'id']
        verbose_name = 'Riga fattura acquisto'
        verbose_name_plural = 'Righe fattura acquisto'


class ScadenzaFattura(models.Model):
    """Scadenza di una fattura cliente (FK a TestataFattura)."""

    fattura = models.ForeignKey(
        TestataFattura, on_delete=models.CASCADE, related_name='scadenze',
    )
    data = models.DateField()
    importo = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f'Fatt. {self.fattura.numero}/{self.fattura.anno} - {self.data} - {self.importo}'

    class Meta:
        ordering = ['data']
        verbose_name = 'Scadenza fattura'
        verbose_name_plural = 'Scadenze fattura'


class ScadenzaFatturaAcquisto(models.Model):
    class ModalitaPagamento(models.TextChoices):
        MP01 = 'MP01', 'Contanti'
        MP02 = 'MP02', 'Assegno'
        MP03 = 'MP03', 'Assegno circolare'
        MP04 = 'MP04', 'Contanti presso tesoreria'
        MP05 = 'MP05', 'Bonifico'
        MP06 = 'MP06', 'Vaglia cambiario'
        MP07 = 'MP07', 'Bollettino bancario'
        MP08 = 'MP08', 'Carta di credito'
        MP09 = 'MP09', 'RID'
        MP10 = 'MP10', 'RID utenze'
        MP11 = 'MP11', 'RID veloce'
        MP12 = 'MP12', 'Riba'
        MP13 = 'MP13', 'MAV'
        MP14 = 'MP14', 'Quietanza erario'
        MP15 = 'MP15', 'Giroconto contabilità speciale'
        MP16 = 'MP16', 'Domiciliazione bancaria'
        MP17 = 'MP17', 'Domiciliazione postale'

    fattura = models.ForeignKey(
        TestataFatturaAcquisto, on_delete=models.CASCADE, related_name='scadenze',
    )
    data_scadenza = models.DateField()
    importo = models.DecimalField(max_digits=10, decimal_places=2)
    modalita_pagamento = models.CharField(
        max_length=10,
        choices=ModalitaPagamento.choices,
        default=ModalitaPagamento.MP05,
        blank=True, null=True,
    )
    iban = models.CharField(max_length=34, blank=True, null=True)

    @property
    def importo_pagato(self):
        total = Decimal('0')
        for p in self.pagamenti.all():
            total += p.importo
        return total

    @property
    def importo_residuo(self):
        return self.importo - self.importo_pagato

    @property
    def stato(self):
        pagato = self.importo_pagato
        if pagato <= 0:
            return 'aperta'
        if pagato >= self.importo:
            return 'pagata'
        return 'parziale'

    def __str__(self):
        return f'Fatt.acq. {self.fattura.numero}/{self.fattura.anno} - {self.data_scadenza} - {self.importo}'

    class Meta:
        ordering = ['data_scadenza']
        verbose_name = 'Scadenza fattura acquisto'
        verbose_name_plural = 'Scadenze fattura acquisto'


class PagamentoScadenzaAcquisto(models.Model):
    scadenza = models.ForeignKey(
        ScadenzaFatturaAcquisto, on_delete=models.CASCADE,
        related_name='pagamenti',
    )
    data_pagamento = models.DateField(default=now)
    importo = models.DecimalField(max_digits=10, decimal_places=2)
    modalita_pagamento = models.CharField(
        max_length=10,
        choices=ScadenzaFatturaAcquisto.ModalitaPagamento.choices,
        default=ScadenzaFatturaAcquisto.ModalitaPagamento.MP05,
        blank=True, null=True,
    )
    note = models.TextField(blank=True, null=True)

    def __str__(self):
        return f'{self.data_pagamento} - {self.importo}'

    class Meta:
        ordering = ['-data_pagamento']
        verbose_name = 'Pagamento scadenza acquisto'
        verbose_name_plural = 'Pagamenti scadenze acquisto'


# ---------------------------------------------------------------------------
# Agente <-> Fattura (M2M through con provvigione)
# ---------------------------------------------------------------------------


class AgenteFattura(models.Model):
    agente = models.ForeignKey(
        'anagrafiche.Agente', on_delete=models.CASCADE,
    )
    fattura = models.ForeignKey(
        TestataFattura, on_delete=models.CASCADE,
    )
    provvigione = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
    )

    def __str__(self):
        return f'{self.agente} - {self.fattura} - {self.provvigione}%'

    class Meta:
        verbose_name = 'Agente fattura'
        verbose_name_plural = 'Agenti fattura'
        unique_together = [('agente', 'fattura')]


# ---------------------------------------------------------------------------
# Signals: ricalcolo imponibile / totali
# ---------------------------------------------------------------------------


@receiver(post_save, sender=TestataOfferta)
def _recalc_offerta(sender, instance, **kwargs):
    _recalc_imponibile(instance)


@receiver(post_save, sender=TestataOrdine)
def _recalc_ordine(sender, instance, **kwargs):
    _recalc_imponibile(instance)


@receiver(post_save, sender=TestataFattura)
def _recalc_fattura(sender, instance, **kwargs):
    _recalc_imponibile(instance)


@receiver(post_save, sender=TestataNotaCredito)
def _recalc_nota_credito(sender, instance, **kwargs):
    _recalc_imponibile(instance)


@receiver(post_save, sender=TestataDdt)
def _recalc_ddt(sender, instance, **kwargs):
    _recalc_imponibile(instance)


@receiver(post_save, sender=TestataDdtFornitore)
def _recalc_ddt_fornitore(sender, instance, **kwargs):
    _recalc_imponibile(instance)


@receiver([post_save, post_delete], sender=RigaDocumento)
def _propaga_riga(sender, instance, **kwargs):
    """Ricalcola l'imponibile della testata di appartenenza.

    Su ``post_delete`` la testata può essere stata già cancellata (cascade):
    in quel caso la query restituisce DoesNotExist e si ignora silenziosamente.
    """
    mappings = [
        ('testata_offerta_id', TestataOfferta),
        ('testata_ordine_id', TestataOrdine),
        ('testata_ddt_id', TestataDdt),
        ('testata_fattura_id', TestataFattura),
        ('testata_nota_credito_id', TestataNotaCredito),
        ('testata_ddt_fornitore_id', TestataDdtFornitore),
    ]
    for attr_id, model in mappings:
        pk = getattr(instance, attr_id, None)
        if not pk:
            continue
        try:
            testata = model.objects.get(pk=pk)
        except model.DoesNotExist:
            continue
        _recalc_imponibile(testata)


@receiver(post_save, sender=TestataFatturaAcquisto)
def _recalc_fattura_acquisto(sender, instance, **kwargs):
    imponibile = Decimal('0.00')
    imposta = Decimal('0.00')
    for row in instance.righe.all():
        row_imponibile = Decimal(str(row.prezzo_unitario)) * Decimal(str(row.quantita))
        if row.sconto_percentuale:
            row_imponibile *= (
                Decimal('1') - Decimal(str(row.sconto_percentuale)) / Decimal('100')
            )
        imponibile += row_imponibile
        if row.aliquota_iva:
            imposta += row_imponibile * row.aliquota_iva.aliquota / Decimal('100')
    TestataFatturaAcquisto.objects.filter(pk=instance.pk).update(
        imponibile=imponibile,
        imposta=imposta,
        totale_documento=imponibile + imposta,
    )


@receiver([post_save, post_delete], sender=RigaFatturaAcquisto)
def _propaga_riga_acquisto(sender, instance, **kwargs):
    if not instance.testata_id:
        return
    try:
        testata = TestataFatturaAcquisto.objects.get(pk=instance.testata_id)
    except TestataFatturaAcquisto.DoesNotExist:
        return
    _recalc_fattura_acquisto(TestataFatturaAcquisto, testata)


def _recalc_stato_pagamento_fattura_acquisto(fattura):
    """Aggiorna ``pagata`` / ``data_pagamento`` in base ai pagamenti delle scadenze."""
    scadenze = list(fattura.scadenze.all())
    if not scadenze:
        return
    tutte_pagate = all(s.importo_residuo <= 0 for s in scadenze)
    if tutte_pagate:
        ultimo = None
        for s in scadenze:
            for p in s.pagamenti.all():
                if ultimo is None or p.data_pagamento > ultimo:
                    ultimo = p.data_pagamento
        TestataFatturaAcquisto.objects.filter(pk=fattura.pk).update(
            pagata=True, data_pagamento=ultimo,
        )
    else:
        TestataFatturaAcquisto.objects.filter(pk=fattura.pk).update(
            pagata=False, data_pagamento=None,
        )


@receiver([post_save, post_delete], sender=PagamentoScadenzaAcquisto)
def _propaga_pagamento_scadenza(sender, instance, **kwargs):
    try:
        fattura = instance.scadenza.fattura
    except (ScadenzaFatturaAcquisto.DoesNotExist, TestataFatturaAcquisto.DoesNotExist):
        return
    _recalc_stato_pagamento_fattura_acquisto(fattura)


# ---------------------------------------------------------------------------
# Messaggi SDI (notifiche e log invii)
# ---------------------------------------------------------------------------


class MessaggioSdi(models.Model):
    """Log dei messaggi scambiati con il Sistema di Interscambio.

    Una fattura ne accumula molti: ``invio`` (l'invio effettuato),
    ``RC`` (ricevuta consegna), ``NS`` (notifica scarto), ``MC`` (mancata
    consegna), ``NE`` (notifica esito), ``DT`` (decorrenza termini), ecc.

    Il body XML/raw può essere allegato per rendere il record
    autosufficiente (utile per audit fiscali).
    """

    class Tipo(models.TextChoices):
        INVIO = 'invio', 'Invio (uscita verso SDI)'
        RC = 'RC', 'RC - Ricevuta consegna'
        NS = 'NS', 'NS - Notifica scarto'
        MC = 'MC', 'MC - Mancata consegna'
        NE = 'NE', 'NE - Notifica esito'
        DT = 'DT', 'DT - Decorrenza termini'
        AT = 'AT', 'AT - Attestazione trasmissione'
        ALTRO = 'altro', 'Altro / non classificato'

    fattura = models.ForeignKey(
        TestataFattura, on_delete=models.CASCADE, related_name='messaggi_sdi',
    )
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    timestamp = models.DateTimeField(auto_now_add=True)
    id_trasmissione = models.CharField(max_length=80, blank=True, default='')
    descrizione = models.CharField(max_length=255, blank=True, default='')
    payload = models.TextField(
        blank=True, default='',
        help_text="Body XML / raw del messaggio (notifica SDI o invio).",
    )
    direzione = models.CharField(
        max_length=10,
        choices=[('out', 'Uscita'), ('in', 'Ingresso')],
        default='in',
    )

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Messaggio SDI'
        verbose_name_plural = 'Messaggi SDI'

    def __str__(self):
        return f'{self.fattura} — {self.tipo} ({self.timestamp:%d/%m/%Y})'
