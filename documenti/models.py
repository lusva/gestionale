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
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction

from tenancy.models import TenantModel
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from datetime import datetime

from django.utils.timezone import is_aware, localdate, localtime, now


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


def _anno_documento(data_documento):
    """Anno di numerazione: quello della data del documento (ora locale),
    oppure l'anno di oggi (ora locale, non UTC) se la data manca."""
    if isinstance(data_documento, datetime):
        if is_aware(data_documento):
            data_documento = localtime(data_documento)
        return data_documento.year
    if data_documento:
        return data_documento.year
    return localdate().year


def _vincolo_numero(nome, con_sezionale=False):
    """Unicità del numero per anno (e sezionale): impedisce numeri doppi
    anche se due salvataggi concorrenti leggono lo stesso ultimo numero."""
    campi = ['anno', 'sezionale', 'numero'] if con_sezionale else ['anno', 'numero']
    return models.UniqueConstraint(fields=['azienda', *campi], name=f'documenti_{nome}_numero_unico')


def _recalc_imponibile(testata, related_name='righe'):
    """Ricalcola imponibile sommando ``importo_unitario * quantita`` delle righe."""
    imponibile = Decimal('0.00')
    for row in getattr(testata, related_name).all():
        imponibile += Decimal(str(row.importo_unitario)) * Decimal(str(row.quantita))
    type(testata).objects.filter(pk=testata.pk).update(imponibile=imponibile)


# ---------------------------------------------------------------------------
# Testata astratta comune
# ---------------------------------------------------------------------------


class TestataDocumento(TenantModel):
    numero = models.PositiveIntegerField(editable=False)
    anno = models.PositiveIntegerField(editable=False)
    data_documento = models.DateField(default=localdate, null=True, blank=True)
    data_registrazione = models.DateField(default=localdate, null=True, blank=True)
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

    # True per i documenti con progressivi separati per sezionale
    NUMERAZIONE_PER_SEZIONALE = False

    def clean(self):
        if not self.cliente and not self.fornitore:
            raise ValidationError(
                'Devi specificare almeno un Cliente o un Fornitore.'
            )

    def save(self, *args, **kwargs):
        """Al primo salvataggio assegna anno (dalla data del documento) e
        numero progressivo: lettura dell'ultimo numero (con lock) e INSERT
        stanno nella stessa transazione, così il lock copre anche il save."""
        if not self._state.adding:
            return super().save(*args, **kwargs)
        with transaction.atomic():
            anno = _anno_documento(self.data_documento)
            sezionale = None
            if self.NUMERAZIONE_PER_SEZIONALE:
                self.sezionale = self.sezionale or ''
                sezionale = self.sezionale
            self.numero = _next_numero_per_anno(type(self), anno, sezionale=sezionale)
            self.anno = anno
            super().save(*args, **kwargs)

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
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


    def __str__(self):
        return f'Offerta {self.numero}/{self.anno} - {self.cliente or self.fornitore or ""}'

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['-anno', '-numero']
        verbose_name = 'Offerta'
        verbose_name_plural = 'Offerte'
        constraints = [_vincolo_numero('offerta')]


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


    def __str__(self):
        return f'Ordine {self.numero}/{self.anno} - {self.cliente or self.fornitore or ""}'

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['-anno', '-numero']
        verbose_name = 'Ordine'
        verbose_name_plural = 'Ordini'
        constraints = [_vincolo_numero('ordine')]


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


    def __str__(self):
        return f'DDT {self.numero}/{self.anno} - {self.cliente or ""}'

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['-anno', '-numero']
        verbose_name = 'DDT'
        verbose_name_plural = 'DDT'
        constraints = [_vincolo_numero('ddt')]


class DocumentoSdi(TenantModel):
    """Campi e stato dell'invio al Sistema di Interscambio, comuni a fatture
    e note di credito (``invio.invia_al_sdi``, ``sdi_pull``)."""

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
        RIFIUTATA = 'rifiutata', 'Rifiutata dal committente (esito EC02)'

    # stati da cui la fattura, corretta, si può rinviare al SDI
    SDI_STATI_REINVIO = (SdiStato.SCARTATA, SdiStato.RIFIUTATA)

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
    tentativo_sdi = models.PositiveSmallIntegerField(
        default=0,
        help_text="Numero di reinvii al SDI dopo scarto/rifiuto: entra nel "
                  "progressivo del file, che il SDI non accetta due volte.",
    )

    @property
    def trasmessa_sdi(self):
        """True se la fattura è partita verso il SDI (anche se poi scartata o
        rifiutata):
        il suo numero è ormai noto al SDI e non va riusato."""
        return self.sdi_stato != self.SdiStato.NON_INVIATA

    @property
    def emessa_sdi(self):
        """True se il SDI l'ha presa in carico (inviata/ricevuta/consegnata o
        in giacenza): la fattura è emessa e non va più modificata. Una
        fattura scartata o rifiutata invece si corregge e si rinvia."""
        return (self.sdi_stato != self.SdiStato.NON_INVIATA
                and self.sdi_stato not in self.SDI_STATI_REINVIO)

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        abstract = True


class TestataFattura(DocumentoSdi, TestataDocumento):
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
    NUMERAZIONE_PER_SEZIONALE = True

    # titolo in testa al PDF secondo il tipo documento (gli altri: «Fattura»)
    _TITOLI_STAMPA = {
        'TD02': 'Fattura di acconto',
        'TD03': 'Parcella di acconto',
        'TD05': 'Nota di debito',
        'TD06': 'Parcella',
        'TD16': 'Integrazione fattura',
        'TD17': 'Autofattura', 'TD18': 'Autofattura', 'TD19': 'Autofattura',
        'TD20': 'Autofattura', 'TD21': 'Autofattura', 'TD27': 'Autofattura',
        'TD24': 'Fattura differita', 'TD25': 'Fattura differita',
    }

    @property
    def titolo_stampa(self):
        return self._TITOLI_STAMPA.get(self.tipo_documento, 'Fattura')

    nota_credito_collegata = models.ManyToManyField(
        'TestataNotaCredito', blank=True, related_name='fatture_origine',
    )
    pagata = models.BooleanField(default=False)
    data_pagamento = models.DateField(null=True, blank=True)

    def __str__(self):
        sez = f'{self.sezionale}/' if self.sezionale else ''
        return f'Fattura {sez}{self.numero}/{self.anno} - {self.cliente or ""}'

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['-anno', '-numero']
        verbose_name = 'Fattura'
        verbose_name_plural = 'Fatture'
        constraints = [_vincolo_numero('fattura', con_sezionale=True)]


class TestataNotaCredito(DocumentoSdi, TestataDocumento):
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

    NUMERAZIONE_PER_SEZIONALE = True
    # Le note nuove propongono un sezionale proprio: nel <Numero> della
    # FatturaPA «NC/1/2026», mai confondibile con la fattura 1/2026.
    SEZIONALE_PREDEFINITO = 'NC'
    titolo_stampa = 'Nota di credito'


    def __str__(self):
        sez = f'{self.sezionale}/' if self.sezionale else ''
        return f'Nota Credito {sez}{self.numero}/{self.anno} - {self.cliente or ""}'

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['-anno', '-numero']
        verbose_name = 'Nota di credito'
        verbose_name_plural = 'Note di credito'
        constraints = [_vincolo_numero('nota_credito', con_sezionale=True)]


# ---------------------------------------------------------------------------
# Ciclo passivo (DDT Fornitore + Fattura Acquisto)
# ---------------------------------------------------------------------------


class TestataDdtFornitore(TestataDocumento):
    numero_palette = models.PositiveIntegerField(default=0)
    peso_netto = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    peso_lordo = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    vettore = models.CharField(max_length=50, blank=True, default='')


    def __str__(self):
        return f'DDT Fornitore {self.numero}/{self.anno} - {self.fornitore or ""}'

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['-anno', '-numero']
        verbose_name = 'DDT fornitore'
        verbose_name_plural = 'DDT fornitore'
        constraints = [_vincolo_numero('ddt_fornitore')]


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
    # PDF o scansione della fattura ricevuta (cartacea, fornitore estero…):
    # si scarica solo con documenti.vedi (gestionale/media_views.py).
    allegato = models.FileField(
        upload_to='fatture_acquisto_allegati/', blank=True, null=True,
    )

    @property
    def da_xml(self) -> bool:
        """Importata da FatturaPA: importi e righe fanno fede all'XML."""
        return bool(self.xml_originale)

    def __str__(self):
        return (
            f'Fattura acquisto {self.numero}/{self.anno} - '
            f'{self.fornitore or ""} {self.numero_fornitore or ""}'
        )

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['-anno', '-numero']
        verbose_name = 'Fattura acquisto'
        verbose_name_plural = 'Fatture acquisto'
        constraints = [
            _vincolo_numero('fattura_acquisto'),
            models.UniqueConstraint(
                fields=['azienda', 'fornitore', 'numero_fornitore', 'data_fornitore'],
                name='documenti_fattura_acq_unique_per_fornitore',
                condition=models.Q(numero_fornitore__isnull=False),
            ),
        ]


# ---------------------------------------------------------------------------
# Righe documento (ciclo attivo + DDT fornitore)
# ---------------------------------------------------------------------------


class RigaDocumento(TenantModel):
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
        on_delete=models.PROTECT,
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
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['numero_riga', 'id']
        verbose_name = 'Riga documento'
        verbose_name_plural = 'Righe documento'


# ---------------------------------------------------------------------------
# Righe / scadenze / pagamenti fattura acquisto
# ---------------------------------------------------------------------------


class RigaFatturaAcquisto(TenantModel):
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
        on_delete=models.PROTECT, blank=True, null=True,
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
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['numero_riga', 'id']
        verbose_name = 'Riga fattura acquisto'
        verbose_name_plural = 'Righe fattura acquisto'


class ScadenzaFattura(TenantModel):
    """Scadenza di una fattura cliente (FK a TestataFattura). Gli incassi,
    anche parziali, sono ``IncassoScadenza``: il credito aperto è il
    residuo, non l'importo."""

    fattura = models.ForeignKey(
        TestataFattura, on_delete=models.CASCADE, related_name='scadenze',
    )
    data = models.DateField()
    importo = models.DecimalField(max_digits=10, decimal_places=2)

    @property
    def importo_incassato(self):
        # .all(): usa gli incassi già caricati con prefetch_related
        return sum((i.importo for i in self.incassi.all()), Decimal('0'))

    @property
    def importo_residuo(self):
        return self.importo - self.importo_incassato

    @property
    def stato(self):
        incassato = self.importo_incassato
        if incassato <= 0:
            return 'aperta'
        if incassato >= self.importo:
            return 'incassata'
        return 'parziale'

    def __str__(self):
        return f'Fatt. {self.fattura.numero}/{self.fattura.anno} - {self.data} - {self.importo}'

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['data']
        verbose_name = 'Scadenza fattura'
        verbose_name_plural = 'Scadenze fattura'


class ScadenzaFatturaAcquisto(TenantModel):
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
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['data_scadenza']
        verbose_name = 'Scadenza fattura acquisto'
        verbose_name_plural = 'Scadenze fattura acquisto'


def scadenze_acquisto_con_residuo(qs=None):
    """Le scadenze di acquisto con ``pagato_db`` e ``residuo_db`` calcolati
    dal database: una query sola, invece delle proprietà ``importo_pagato`` /
    ``importo_residuo`` che ne fanno una per scadenza."""
    from django.db.models import DecimalField, ExpressionWrapper, F, OuterRef, Subquery, Sum, Value
    from django.db.models.functions import Coalesce

    qs = ScadenzaFatturaAcquisto.objects.all() if qs is None else qs
    importo = DecimalField(max_digits=12, decimal_places=2)
    pagato = (PagamentoScadenzaAcquisto.objects.filter(scadenza=OuterRef('pk'))
              .order_by().values('scadenza').annotate(t=Sum('importo')).values('t'))
    return qs.annotate(
        pagato_db=Coalesce(Subquery(pagato, output_field=importo), Value(Decimal('0')),
                           output_field=importo),
    ).annotate(residuo_db=ExpressionWrapper(F('importo') - F('pagato_db'), output_field=importo))


class PagamentoScadenzaAcquisto(TenantModel):
    scadenza = models.ForeignKey(
        ScadenzaFatturaAcquisto, on_delete=models.CASCADE,
        related_name='pagamenti',
    )
    data_pagamento = models.DateField(default=localdate)
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
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['-data_pagamento']
        verbose_name = 'Pagamento scadenza acquisto'
        verbose_name_plural = 'Pagamenti scadenze acquisto'


class IncassoScadenza(TenantModel):
    """Un incasso (anche parziale) su una scadenza di una fattura cliente.
    I signal tengono ``TestataFattura.pagata`` / ``data_pagamento`` allineati:
    pagata quando tutte le scadenze hanno residuo zero, alla data
    dell'ultimo incasso."""

    scadenza = models.ForeignKey(ScadenzaFattura, on_delete=models.CASCADE, related_name='incassi')
    data = models.DateField(default=localdate)
    importo = models.DecimalField(max_digits=10, decimal_places=2)
    modalita = models.CharField(
        max_length=10, choices=ScadenzaFatturaAcquisto.ModalitaPagamento.choices,
        default=ScadenzaFatturaAcquisto.ModalitaPagamento.MP05, blank=True, null=True,
    )
    note = models.TextField(blank=True, default='')
    # compensazione con una nota di credito che storna la fattura: nasce e
    # sparisce con la nota (``servizi.storna_crediti``), non si tocca a mano
    nota_credito = models.ForeignKey(
        TestataNotaCredito, on_delete=models.CASCADE, blank=True, null=True,
        related_name='compensazioni',
    )
    creato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['data', 'pk']
        verbose_name = 'Incasso'
        verbose_name_plural = 'Incassi'
        constraints = [
            models.CheckConstraint(condition=models.Q(importo__gt=0), name='documenti_incasso_positivo'),
        ]

    def __str__(self):
        return f'{self.data} - {self.importo}'


def scadenze_fattura_con_residuo(qs=None):
    """Le scadenze delle fatture cliente con ``incassato_db`` e ``residuo_db``
    calcolati dal database (gemella di ``scadenze_acquisto_con_residuo``):
    una query sola per scadenziario, cashflow, «Da fare», solleciti."""
    from django.db.models import DecimalField, ExpressionWrapper, F, OuterRef, Subquery, Sum, Value
    from django.db.models.functions import Coalesce

    qs = ScadenzaFattura.objects.all() if qs is None else qs
    importo = DecimalField(max_digits=12, decimal_places=2)
    incassato = (IncassoScadenza.objects.filter(scadenza=OuterRef('pk'))
                 .order_by().values('scadenza').annotate(t=Sum('importo')).values('t'))
    return qs.annotate(
        incassato_db=Coalesce(Subquery(incassato, output_field=importo), Value(Decimal('0')),
                              output_field=importo),
    ).annotate(residuo_db=ExpressionWrapper(F('importo') - F('incassato_db'), output_field=importo))


def crediti_aperti(qs=None):
    """Le scadenze ancora da incassare (residuo > 0) di fatture non pagate:
    la base di ogni conto dei crediti."""
    qs = ScadenzaFattura.objects.all() if qs is None else qs
    return scadenze_fattura_con_residuo(qs.filter(fattura__pagata=False)).filter(residuo_db__gt=0)


def con_incassi(qs):
    """Le fatture con ``incassato_tot``, ``residuo_tot`` (sulle scadenze) e
    ``prima_scadenza``: la prima rata ancora da incassare, o la prima in
    assoluto se è tutto incassato. Sottoquery: niente query per riga."""
    from django.db.models import DecimalField, ExpressionWrapper, F, OuterRef, Subquery, Sum, Value
    from django.db.models.functions import Coalesce

    importo = DecimalField(max_digits=12, decimal_places=2)
    zero = Value(Decimal('0'))
    incassato = (IncassoScadenza.objects.filter(scadenza__fattura=OuterRef('pk')).order_by()
                 .values('scadenza__fattura').annotate(t=Sum('importo')).values('t'))
    scadenze = (ScadenzaFattura.objects.filter(fattura=OuterRef('pk')).order_by()
                .values('fattura').annotate(t=Sum('importo')).values('t'))
    prima_aperta = (crediti_aperti(ScadenzaFattura.objects.filter(fattura=OuterRef('pk')))
                    .order_by('data').values('data')[:1])
    prima = ScadenzaFattura.objects.filter(fattura=OuterRef('pk')).order_by('data').values('data')[:1]
    return qs.annotate(
        incassato_tot=Coalesce(Subquery(incassato, output_field=importo), zero, output_field=importo),
        scadenze_tot=Coalesce(Subquery(scadenze, output_field=importo), zero, output_field=importo),
        prima_scadenza=Coalesce(Subquery(prima_aperta), Subquery(prima)),
    ).annotate(residuo_tot=ExpressionWrapper(F('scadenze_tot') - F('incassato_tot'), output_field=importo))


# ---------------------------------------------------------------------------
# Agente <-> Fattura (M2M through con provvigione)
# ---------------------------------------------------------------------------


class AgenteFattura(TenantModel):
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
        base_manager_name = 'objects'
        default_manager_name = 'objects'
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


_CENTESIMO = Decimal('0.01')


def totali_acquisto(righe) -> dict:
    """Imponibile, IVA e totale di una fattura di acquisto dalle sue righe
    (anche non salvate: la usa l'anteprima del form). Come il riepilogo
    FatturaPA: imponibile di riga al centesimo, IVA calcolata per aliquota
    sul totale di ciascuna e arrotondata una volta sola."""
    gruppi: dict = {}
    for r in righe:
        imponibile = Decimal(str(r.prezzo_unitario or 0)) * Decimal(str(r.quantita or 0))
        if r.sconto_percentuale:
            imponibile *= Decimal('1') - Decimal(str(r.sconto_percentuale)) / Decimal('100')
        iva = r.aliquota_iva
        chiave = (iva.aliquota, iva.natura or '') if iva is not None else (Decimal('0'), '')
        gruppi[chiave] = gruppi.get(chiave, Decimal('0')) + imponibile.quantize(_CENTESIMO)
    dettaglio = []
    for (aliquota, natura), imponibile in sorted(gruppi.items(), key=lambda g: -g[0][0]):
        imposta = (imponibile * aliquota / Decimal('100')).quantize(_CENTESIMO)
        dettaglio.append({'aliquota': aliquota, 'natura': natura,
                          'imponibile': imponibile, 'imposta': imposta})
    imponibile = sum((d['imponibile'] for d in dettaglio), Decimal('0.00'))
    imposta = sum((d['imposta'] for d in dettaglio), Decimal('0.00'))
    return {'imponibile': imponibile, 'imposta': imposta, 'imposta_totale': imposta,
            'totale': imponibile + imposta, 'iva_dettaglio': dettaglio}


@receiver(post_save, sender=TestataFatturaAcquisto)
def _recalc_fattura_acquisto(sender, instance, **kwargs):
    # Una fattura importata da XML porta i suoi importi (che possono
    # includere cassa, bollo, arrotondamenti…): li scrive l'import e restano.
    # Cambiarne stato o categorie di costo non deve toccarli.
    if instance.xml_originale:
        return
    t = totali_acquisto(instance.righe.select_related('aliquota_iva'))
    TestataFatturaAcquisto.objects.filter(pk=instance.pk).update(
        imponibile=t['imponibile'], imposta=t['imposta'], totale_documento=t['totale'],
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


def ricalcola_incasso_fattura(fattura_id) -> None:
    """``pagata`` e ``data_pagamento`` dagli incassi: pagata se tutte le
    scadenze hanno residuo zero, alla data dell'ultimo incasso. Le fatture
    senza scadenze tengono il flag messo a mano."""
    scadenze = list(ScadenzaFattura.objects.filter(fattura_id=fattura_id).prefetch_related('incassi'))
    if not scadenze:
        return
    if all(s.importo_residuo <= 0 for s in scadenze):
        ultimo = max((i.data for s in scadenze for i in s.incassi.all()), default=None)
        TestataFattura.objects.filter(pk=fattura_id).update(pagata=True, data_pagamento=ultimo)
    else:
        TestataFattura.objects.filter(pk=fattura_id).update(pagata=False, data_pagamento=None)


@receiver([post_save, post_delete], sender=IncassoScadenza)
def _propaga_incasso(sender, instance, **kwargs):
    fattura_id = ScadenzaFattura.objects.filter(pk=instance.scadenza_id).values_list(
        'fattura_id', flat=True).first()
    if fattura_id is not None:      # None: scadenza (o fattura) cancellata insieme
        ricalcola_incasso_fattura(fattura_id)


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


class MessaggioSdi(TenantModel):
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

    # il documento del messaggio: una fattura o una nota di credito
    fattura = models.ForeignKey(
        TestataFattura, on_delete=models.CASCADE, related_name='messaggi_sdi',
        blank=True, null=True,
    )
    nota_credito = models.ForeignKey(
        TestataNotaCredito, on_delete=models.CASCADE, related_name='messaggi_sdi',
        blank=True, null=True,
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
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['-timestamp']
        verbose_name = 'Messaggio SDI'
        verbose_name_plural = 'Messaggi SDI'
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(fattura__isnull=False, nota_credito__isnull=True)
                           | models.Q(fattura__isnull=True, nota_credito__isnull=False)),
                name='documenti_messaggiosdi_un_documento',
            ),
        ]

    @property
    def documento(self):
        return self.fattura if self.fattura_id else self.nota_credito

    def __str__(self):
        return f'{self.documento} — {self.tipo} ({self.timestamp:%d/%m/%Y})'


class MessaggioPecElaborato(TenantModel):
    """Email della casella PEC già elaborate da ``sdi_pull``.

    La PEC è quella aziendale, letta anche da persone: ``sdi_pull`` non ne
    tocca i flag (letto/non letto) e tiene qui traccia di cosa ha già
    elaborato. ``chiave`` è il Message-ID della busta PEC (o
    ``uid:<uidvalidity>:<uid>`` se manca). Cancellare una riga fa
    rielaborare l'email alla run successiva, se ancora nella finestra.
    """

    chiave = models.CharField(max_length=255)
    elaborato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        constraints = [
            models.UniqueConstraint(fields=['azienda', 'chiave'], name='documenti_messaggiopecelaborato_chiave_az'),
        ]
        ordering = ['-elaborato_il']
        verbose_name = 'Email PEC elaborata'
        verbose_name_plural = 'Email PEC elaborate'

    def __str__(self):
        return self.chiave


# ---------------------------------------------------------------------------
# Fatture ricorrenti (canoni): modello, righe, emissioni
# ---------------------------------------------------------------------------


def _periodicita_choices():
    from cashflow.models import Periodicita
    return Periodicita.choices


class FatturaRicorrente(TenantModel):
    """Una fattura che si ripete (canone di assistenza, noleggio…): ogni
    periodo ne nasce una vera, datata il giorno in cui si emette. La logica
    è in ``documenti/ricorrenti.py``; le emette il comando
    ``genera_fatture_ricorrenti`` (timer ogni mattina) o il pulsante «Emetti
    ora»."""

    class Competenza(models.TextChoices):
        CORRENTE = 'corrente', 'Periodo corrente (in anticipo)'
        PRECEDENTE = 'precedente', 'Periodo precedente (a consuntivo)'

    nome = models.CharField(max_length=200, help_text='Es. «Canone assistenza JANUS».')
    cliente = models.ForeignKey('clienti.Cliente', on_delete=models.PROTECT,
                                related_name='fatture_ricorrenti')
    attiva = models.BooleanField(default=True)
    periodicita = models.CharField(max_length=20, choices=_periodicita_choices, default='mensile')
    giorno_emissione = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(31)],
        help_text='1–31: nei mesi più corti si emette l\'ultimo giorno (31 = sempre a fine mese).',
    )
    data_inizio = models.DateField(default=localdate)
    data_fine = models.DateField(blank=True, null=True)
    tipo_documento = models.CharField(max_length=10, choices=TestataFattura.TipoDocumento.choices,
                                      default=TestataFattura.TipoDocumento.TD01)
    sezionale = models.CharField(max_length=10, blank=True, default='')
    forme_pagamento = models.ForeignKey('anagrafiche.FormePagamento', on_delete=models.SET_NULL,
                                        blank=True, null=True, related_name='+')
    conto_corrente = models.ForeignKey('anagrafiche.ContoCorrente', on_delete=models.SET_NULL,
                                       blank=True, null=True, related_name='+')
    note = models.TextField(blank=True, default='', help_text='Note in fattura (con i segnaposto).')
    competenza = models.CharField(max_length=20, choices=Competenza.choices,
                                  default=Competenza.CORRENTE)
    invia_sdi = models.BooleanField(default=False)
    invia_email = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['-attiva', 'nome']
        verbose_name = 'Fattura ricorrente'
        verbose_name_plural = 'Fatture ricorrenti'

    def __str__(self):
        return self.nome


class RigaFatturaRicorrente(TenantModel):
    """Riga del modello: stessi campi di ``RigaDocumento``, così il form usa
    le stesse righe (e lo stesso JS) delle fatture."""

    ricorrenza = models.ForeignKey(FatturaRicorrente, on_delete=models.CASCADE, related_name='righe')
    numero_riga = models.PositiveIntegerField(default=0, blank=True, null=True)
    articolo = models.ForeignKey('anagrafiche.Articolo', on_delete=models.SET_NULL,
                                 blank=True, null=True, related_name='+')
    descrizione_libera = models.TextField(max_length=1000, blank=True, null=True)
    quantita = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('1'))
    um = models.CharField(max_length=10, blank=True, null=True, choices=RigaDocumento.UM.choices)
    importo_unitario = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    iva = models.ForeignKey('anagrafiche.PosizioneIva', on_delete=models.PROTECT,
                            blank=True, null=True, related_name='+')

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['numero_riga', 'id']
        verbose_name = 'Riga fattura ricorrente'
        verbose_name_plural = 'Righe fatture ricorrenti'

    def __str__(self):
        return f'{self.ricorrenza_id} - {(self.descrizione_libera or "")[:40]}'


class EmissioneRicorrente(TenantModel):
    """Un periodo di una ricorrenza: emesso (con la fattura) o non riuscito
    (con l'errore, si riprova alla run successiva). Il vincolo su
    (ricorrenza, periodo) è la garanzia che un periodo non si fatturi mai
    due volte."""

    class Esito(models.TextChoices):
        EMESSA = 'emessa', 'Emessa'
        ERRORE = 'errore', 'Non riuscita'
        SALTATA = 'saltata', 'Saltata (ricorrenza sospesa)'

    ricorrenza = models.ForeignKey(FatturaRicorrente, on_delete=models.CASCADE, related_name='emissioni')
    periodo = models.DateField(help_text='Data prevista di emissione del periodo.')
    fattura = models.ForeignKey(TestataFattura, on_delete=models.SET_NULL, blank=True, null=True,
                                related_name='emissioni_ricorrenti')
    esito = models.CharField(max_length=10, choices=Esito.choices)
    errore = models.TextField(blank=True, default='')
    # esito dell'invio automatico (SDI, email), se previsto dalla ricorrenza
    invio = models.TextField(blank=True, default='')
    creata_il = models.DateTimeField(auto_now_add=True)
    aggiornata_il = models.DateTimeField(auto_now=True)

    class Meta:
        base_manager_name = 'objects'
        default_manager_name = 'objects'
        ordering = ['-periodo']
        verbose_name = 'Emissione ricorrente'
        verbose_name_plural = 'Emissioni ricorrenti'
        constraints = [
            models.UniqueConstraint(fields=['azienda', 'ricorrenza', 'periodo'],
                                    name='documenti_emissione_ricorrente_unica'),
        ]

    def __str__(self):
        return f'{self.ricorrenza} — {self.periodo:%d/%m/%Y} ({self.esito})'
