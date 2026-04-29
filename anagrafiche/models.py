"""
Modelli anagrafici / fiscali (ciclo attivo e passivo).

Questa app estende il CRM con i modelli necessari a gestire un gestionale
completo: fornitori, articoli, posizioni IVA, forme di pagamento, conti
correnti, indirizzi multipli, profili fiscali, categorie di costo e i dati
fiscali dell'azienda (intestazione, logo, SDI/PEC).

Il Cliente è definito in ``clienti.models``: qui lo estendiamo via
ForeignKey verso FormePagamento, PosizioneIva, Referente, ProfiloFiscale
e OneToOne verso FatturazioneElettronica (vedi ``clienti.models.Cliente``).
"""
from django.db import models
from django.db.models import Case, Value, When

from .choices import CodiceNazione, ProvinciaItaliana


# ---------------------------------------------------------------------------
# Fatturazione elettronica / fiscale
# ---------------------------------------------------------------------------


class FatturazioneElettronica(models.Model):
    sdi = models.CharField(max_length=7, blank=True, default='0000000')
    pec = models.CharField(max_length=100, blank=True, default='')

    def __str__(self):
        return f'{self.sdi} - {self.pec}'

    class Meta:
        verbose_name = 'Fatturazione elettronica'
        verbose_name_plural = 'Fatturazione elettronica'


class EsigibilitaIva(models.Model):
    class TipoEsigibilitaIva(models.TextChoices):
        I = 'I', 'Esigibilità immediata'
        D = 'D', 'Esigibilità differita'
        S = 'S', 'Scissione dei pagamenti'

    attiva = models.BooleanField(default=False)
    tipo = models.CharField(
        max_length=1,
        choices=TipoEsigibilitaIva.choices,
        default=TipoEsigibilitaIva.I,
    )

    def __str__(self):
        return self.get_tipo_display()

    class Meta:
        verbose_name_plural = 'Esigibilità Iva'


class PosizioneIva(models.Model):
    class EsigibilitaIvaChoice(models.TextChoices):
        I = 'I', 'Esigibilità immediata'
        D = 'D', 'Esigibilità differita'
        S = 'S', 'Scissione dei pagamenti'

    class NaturaIva(models.TextChoices):
        N = '', 'Standard (nessuna natura)'
        N1 = 'N1', 'N1 - Escluse ex art. 15'
        N2_1 = 'N2.1', "N2.1 - Non soggette art. 7 lett. a)"
        N2_2 = 'N2.2', "N2.2 - Non soggette art. 7 lett. b)"
        N3_1 = 'N3.1', 'N3.1 - Non imponibili esportazioni'
        N3_2 = 'N3.2', 'N3.2 - Non imponibili cessioni intracomunitarie'
        N3_3 = 'N3.3', 'N3.3 - Non imponibili cessioni San Marino'
        N3_4 = 'N3.4', "N3.4 - Non imponibili operazioni assimilate a esportazione"
        N3_5 = 'N3.5', 'N3.5 - Non imponibili acquisti intracomunitari'
        N3_6 = 'N3.6', 'N3.6 - Non imponibili importazioni'
        N4 = 'N4', 'N4 - Esenti'
        N5 = 'N5', 'N5 - Regime del margine / IVA non esposta'
        N6_1 = 'N6.1', 'N6.1 - Reverse charge rottami'
        N6_2 = 'N6.2', 'N6.2 - Reverse charge oro e argento'
        N6_3 = 'N6.3', 'N6.3 - Reverse charge subappalto edile'
        N6_4 = 'N6.4', 'N6.4 - Reverse charge cessione fabbricati'
        N6_5 = 'N6.5', 'N6.5 - Reverse charge telefoni cellulari'
        N6_6 = 'N6.6', 'N6.6 - Reverse charge prodotti elettronici'
        N6_7 = 'N6.7', 'N6.7 - Reverse charge prestazioni settore edile'
        N6_8 = 'N6.8', 'N6.8 - Reverse charge settore energetico'
        N6_9 = 'N6.9', 'N6.9 - Reverse charge altri casi'
        N7 = 'N7', "N7 - IVA assolta all'estero"

    descrizione = models.CharField(max_length=255)
    aliquota = models.DecimalField(max_digits=4, decimal_places=2)
    esigibilita_iva = models.CharField(
        max_length=1,
        choices=EsigibilitaIvaChoice.choices,
        default=EsigibilitaIvaChoice.I,
    )
    scissione_pagamenti = models.BooleanField(default=False)
    reverse_charge = models.BooleanField(default=False)
    bollo = models.BooleanField(default=False)
    esente = models.BooleanField(default=False)
    natura = models.CharField(
        max_length=10,
        choices=NaturaIva.choices,
        default=NaturaIva.N,
        blank=True,
    )

    def __str__(self):
        return self.descrizione

    class Meta:
        ordering = ['aliquota', 'descrizione']
        verbose_name = 'Posizione IVA'
        verbose_name_plural = 'Posizioni IVA'


class CassaPrevidenziale(models.Model):
    class TipoCassaPrevidenziale(models.TextChoices):
        TC01 = 'TC01', 'Cassa avvocati e procuratori legali'
        TC02 = 'TC02', 'Cassa dottori commercialisti'
        TC03 = 'TC03', 'Cassa geometri'
        TC04 = 'TC04', 'Cassa ingegneri e architetti'
        TC05 = 'TC05', 'Cassa del notariato'
        TC06 = 'TC06', 'Cassa ragionieri e periti commerciali'
        TC07 = 'TC07', 'ENASARCO'
        TC08 = 'TC08', 'ENPACL - Consulenti del lavoro'
        TC09 = 'TC09', 'ENPAM - Medici'
        TC10 = 'TC10', 'ENPAF - Farmacisti'
        TC11 = 'TC11', 'ENPAV - Veterinari'
        TC12 = 'TC12', 'ENPAIA - Impiegati agricoltura'
        TC13 = 'TC13', 'Fondo spedizionieri e agenzie marittime'
        TC14 = 'TC14', 'INPGI - Giornalisti'
        TC15 = 'TC15', 'ONAOSI'
        TC16 = 'TC16', 'CASAGIT'
        TC17 = 'TC17', 'EPPI - Periti industriali'
        TC18 = 'TC18', 'EPAP - Pluricategoriale'
        TC19 = 'TC19', 'ENPAB - Biologi'
        TC20 = 'TC20', 'ENPAPI - Infermieri'
        TC21 = 'TC21', 'ENPAP - Psicologi'
        TC22 = 'TC22', 'INPS'

    attiva = models.BooleanField(default=False)
    tipo = models.CharField(
        max_length=10,
        choices=TipoCassaPrevidenziale.choices,
        default=TipoCassaPrevidenziale.TC01,
    )
    aliquota_cassa = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    percentuale_imponibile = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
    )
    codice_iva = models.ForeignKey(
        PosizioneIva,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    def __str__(self):
        return f'{self.tipo} - {self.aliquota_cassa}%'

    class Meta:
        verbose_name = 'Cassa previdenziale'
        verbose_name_plural = 'Casse previdenziali'


class ProfiloFiscale(models.Model):
    class RegimeFiscale(models.TextChoices):
        RF01 = 'RF01', 'Ordinario'
        RF02 = 'RF02', 'Contribuenti minimi (L. 244/2007)'
        RF04 = 'RF04', 'Agricoltura e pesca (art. 34, DPR 633/72)'
        RF05 = 'RF05', 'Vendita sali e tabacchi'
        RF06 = 'RF06', 'Commercio fiammiferi'
        RF07 = 'RF07', 'Editoria'
        RF08 = 'RF08', 'Telefonia pubblica'
        RF09 = 'RF09', 'Documenti trasporto pubblico'
        RF10 = 'RF10', 'Intrattenimenti e giochi'
        RF11 = 'RF11', 'Agenzie di viaggio (art. 74-ter)'
        RF12 = 'RF12', 'Agriturismo'
        RF13 = 'RF13', 'Vendite a domicilio'
        RF14 = 'RF14', "Rivendita beni usati / arte / antiquariato"
        RF15 = 'RF15', "Agenzie vendite all'asta arte"
        RF16 = 'RF16', 'IVA per cassa P.A.'
        RF17 = 'RF17', 'IVA per cassa (DL 83/2012)'
        RF18 = 'RF18', 'Altro'
        RF19 = 'RF19', 'Forfettario (L. 190/2014)'

    regime_fiscale = models.CharField(
        max_length=5,
        choices=RegimeFiscale.choices,
        default=RegimeFiscale.RF01,
    )
    esigibilita_iva = models.ForeignKey(
        EsigibilitaIva, on_delete=models.SET_NULL, null=True, blank=True,
    )
    cassa_previdenziale = models.ForeignKey(
        CassaPrevidenziale, on_delete=models.SET_NULL, null=True, blank=True,
    )

    def __str__(self):
        return self.get_regime_fiscale_display()

    class Meta:
        verbose_name = 'Profilo fiscale'
        verbose_name_plural = 'Profili fiscali'


# ---------------------------------------------------------------------------
# Forme di pagamento e scadenze
# ---------------------------------------------------------------------------


class FormePagamento(models.Model):
    class ModalitaPagamentoType(models.TextChoices):
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

    tipo_pagamento = models.CharField(max_length=50, null=True, blank=True)
    conto_corrente_cliente = models.BooleanField(default=False)
    modalita_pagamento = models.CharField(
        max_length=50,
        choices=ModalitaPagamentoType.choices,
        default=ModalitaPagamentoType.MP05,
    )

    def __str__(self):
        return self.tipo_pagamento or self.get_modalita_pagamento_display()

    class Meta:
        verbose_name = 'Forma di pagamento'
        verbose_name_plural = 'Forme di pagamento'


class Scadenza(models.Model):
    """Regola di scadenza (in giorni / % sul totale) per una FormaPagamento."""

    numero_giorni = models.IntegerField()
    percentuale = models.DecimalField(max_digits=5, decimal_places=2)
    fine_mese = models.BooleanField(default=False)
    forme_pagamento = models.ForeignKey(
        FormePagamento,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='scadenze',
    )
    numero_giorni_fm = models.IntegerField(default=0)

    def __str__(self):
        return (
            f'{self.forme_pagamento} — {self.numero_giorni} gg — '
            f'{self.percentuale}%'
        )

    class Meta:
        ordering = ['numero_giorni']
        verbose_name_plural = 'Scadenze'


# ---------------------------------------------------------------------------
# Anagrafica azienda e base astratta (fornitori, agenti)
# ---------------------------------------------------------------------------


class AnagraficaAzienda(models.Model):
    """Singola anagrafica dell'azienda titolare del gestionale."""

    ragione_sociale = models.CharField(max_length=255, default='')
    indirizzo_legale = models.CharField(max_length=255, default='')
    comune_legale = models.CharField(max_length=50, default='')
    cap_legale = models.CharField(max_length=5, default='')
    prov_legale = models.CharField(
        max_length=3,
        choices=ProvinciaItaliana.choices,
        default='',
    )
    indirizzo_op = models.CharField(max_length=255, blank=True, default='')
    comune_op = models.CharField(max_length=50, blank=True, default='')
    cap_op = models.CharField(max_length=5, blank=True, default='')
    email = models.EmailField(default='')
    telefono = models.CharField(max_length=20, default='')
    partita_iva = models.CharField(
        max_length=11, blank=True, null=True, unique=True,
    )
    codice_fiscale = models.CharField(
        max_length=16, blank=True, null=True, unique=True,
    )
    fatturazione_elettronica = models.OneToOneField(
        FatturazioneElettronica,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    logo = models.ImageField(upload_to='anagrafica/', blank=True, null=True)
    intestazione_documenti = models.CharField(
        max_length=255, blank=True, null=True,
    )
    profilo_fiscale = models.OneToOneField(
        ProfiloFiscale,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    contenuto_footer = models.TextField(blank=True, null=True)

    modulo_magazzino_attivo = models.BooleanField(
        default=True,
        help_text=(
            "Se disattivato, i documenti non generano movimenti di "
            "carico/scarico e la voce 'Magazzino' viene nascosta dal menu."
        ),
    )

    # Firma digitale PDF (pyHanko). Il P12 va caricato manualmente; la
    # password resta in chiaro su DB — accettabile per dev, in prod
    # spostare a una keyring esterna o lette da settings/env.
    certificato_p12 = models.FileField(
        upload_to='certificati/',
        blank=True,
        null=True,
        help_text="File .p12/.pfx per la firma digitale PAdES dei PDF.",
    )
    certificato_password = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Passphrase del file .p12. Lasciare vuoto se non si firmano i PDF.",
    )
    firma_motivo = models.CharField(
        max_length=200,
        blank=True,
        default='Documento emesso elettronicamente',
        help_text="Stringa 'Reason' inserita nel sigillo PAdES.",
    )

    # === Integrazione SDI / PEC ===
    class SdiProvider(models.TextChoices):
        DISABILITATO = 'disabilitato', 'Disabilitato'
        MOCK = 'mock', 'Mock (dev/test)'
        PEC = 'pec', 'PEC SMTP/IMAP'

    sdi_provider = models.CharField(
        max_length=20,
        choices=SdiProvider.choices,
        default=SdiProvider.DISABILITATO,
        help_text="Backend per invio fatture al Sistema di Interscambio.",
    )
    pec_mittente = models.EmailField(
        blank=True, default='',
        help_text="Indirizzo PEC del mittente (FROM degli invii al SDI).",
    )
    pec_destinatario_sdi = models.EmailField(
        blank=True, default='sdi01@pec.fatturapa.it',
        help_text="Casella PEC del SDI per l'invio (default sdi01@pec.fatturapa.it).",
    )
    pec_smtp_host = models.CharField(max_length=200, blank=True, default='')
    pec_smtp_port = models.PositiveIntegerField(null=True, blank=True)
    pec_smtp_user = models.CharField(max_length=200, blank=True, default='')
    pec_smtp_password = models.CharField(max_length=200, blank=True, default='')
    pec_smtp_use_tls = models.BooleanField(default=True)
    pec_imap_host = models.CharField(max_length=200, blank=True, default='')
    pec_imap_port = models.PositiveIntegerField(null=True, blank=True, default=993)
    pec_imap_user = models.CharField(max_length=200, blank=True, default='')
    pec_imap_password = models.CharField(max_length=200, blank=True, default='')
    pec_imap_folder = models.CharField(
        max_length=100, blank=True, default='INBOX',
        help_text="Cartella IMAP da cui leggere notifiche e fatture in entrata.",
    )

    def __str__(self):
        return self.ragione_sociale or 'Azienda non configurata'

    @classmethod
    def current(cls):
        """Singleton helper: restituisce la prima anagrafica o ne crea una vuota."""
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return obj

    class Meta:
        verbose_name = 'Anagrafica azienda'
        verbose_name_plural = 'Anagrafiche azienda'


class AbstractAnagrafica(models.Model):
    """Base comune per Fornitore, Agente (e storicamente Cliente in pas)."""

    ragione_sociale = models.CharField(max_length=255, blank=True, null=True)
    nome = models.CharField(max_length=100, blank=True, null=True)
    cognome = models.CharField(max_length=100, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    telefono = models.CharField(max_length=20, blank=True, null=True)
    partita_iva = models.CharField(
        max_length=16, blank=True, null=True, unique=True,
    )
    codice_fiscale = models.CharField(
        max_length=16, blank=True, null=True, unique=True,
    )
    fatturazione_elettronica = models.OneToOneField(
        FatturazioneElettronica,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
    )
    soft_delete = models.BooleanField(default=False)

    class Meta:
        abstract = True


class Fornitore(AbstractAnagrafica):

    def __str__(self):
        if self.ragione_sociale:
            return self.ragione_sociale
        return f'{self.nome or ""} {self.cognome or ""}'.strip() or 'Fornitore'

    class Meta:
        verbose_name_plural = 'Fornitori'
        ordering = [
            Case(When(cognome__isnull=True, then=Value(1)), default=Value(0)),
            Case(When(cognome='', then=Value(1)), default=Value(0)),
            'cognome',
            Case(When(ragione_sociale__isnull=True, then=Value(1)), default=Value(0)),
            Case(When(ragione_sociale='', then=Value(1)), default=Value(0)),
            'ragione_sociale',
        ]


class Referente(models.Model):
    numero = models.PositiveIntegerField(primary_key=True, default=1)
    nome = models.CharField(max_length=100, blank=True, default='')
    cognome = models.CharField(max_length=100, blank=True, default='')

    def __str__(self):
        return f'{self.numero} - {self.nome} {self.cognome}'.strip()

    class Meta:
        verbose_name_plural = 'Referenti'


class Agente(AbstractAnagrafica):
    provvigione_default = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
    )

    def __str__(self):
        full = f'{self.nome or ""} {self.cognome or ""}'.strip()
        return full or self.ragione_sociale or 'Agente'

    class Meta:
        verbose_name_plural = 'Agenti'


# ---------------------------------------------------------------------------
# Indirizzi e conti correnti (riferibili a Cliente, Fornitore o Azienda)
# ---------------------------------------------------------------------------


class Indirizzo(models.Model):
    indirizzo = models.CharField(max_length=255, blank=True, null=True)
    cap = models.CharField(max_length=5, blank=True, null=True)
    comune = models.CharField(max_length=50, blank=True, null=True)
    provincia = models.CharField(
        max_length=3, choices=ProvinciaItaliana.choices, blank=True, null=True,
    )
    nazione = models.CharField(
        max_length=50,
        choices=CodiceNazione.choices,
        default=CodiceNazione.IT,
        blank=True,
        null=True,
    )
    cliente = models.ForeignKey(
        'clienti.Cliente',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='indirizzi',
    )
    fornitore = models.ForeignKey(
        Fornitore,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='indirizzi',
    )
    sede = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.indirizzo or ""} {self.comune or ""} {self.provincia or ""}'.strip()

    class Meta:
        verbose_name_plural = 'Indirizzi'


class ContoCorrente(models.Model):
    banca = models.CharField(max_length=150, blank=True, default='')
    intestatario = models.CharField(max_length=150, blank=True, default='')
    iban = models.CharField(max_length=27, default='')
    bic_swift = models.CharField(max_length=11, blank=True, default='')
    cliente = models.ForeignKey(
        'clienti.Cliente',
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='conti_correnti',
    )
    fornitore = models.ForeignKey(
        Fornitore,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='conti_correnti',
    )
    anagrafica_azienda = models.ForeignKey(
        AnagraficaAzienda,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name='conti_correnti',
    )
    default = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.banca} — {self.iban}'

    class Meta:
        verbose_name = 'Conto corrente'
        verbose_name_plural = 'Conti correnti'


# ---------------------------------------------------------------------------
# Articoli e categorie costo
# ---------------------------------------------------------------------------


class Articolo(models.Model):
    class UM(models.TextChoices):
        PZ = 'PZ', 'PZ - Pezzi'
        MQ = 'MQ', 'MQ - Metri quadri'
        ML = 'ML', 'ML - Metri lineari'
        KG = 'KG', 'KG - Chilogrammi'
        LT = 'LT', 'LT - Litri'
        ORE = 'ORE', 'ORE'
        N = 'N', 'N'

    codice = models.CharField(max_length=20, default='')
    descrizione = models.CharField(max_length=255, blank=True, default='')
    scelta = models.CharField(max_length=30, blank=True, default='')
    obsoleto = models.BooleanField(default=False)
    um = models.CharField(
        max_length=10, blank=True, choices=UM.choices, default=UM.PZ,
    )
    prezzo_listino = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, blank=True,
    )
    posizione_iva = models.ForeignKey(
        PosizioneIva,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='articoli',
    )

    def __str__(self):
        return f'{self.codice} - {self.descrizione}'

    class Meta:
        ordering = ['codice']
        verbose_name_plural = 'Articoli'


class CategoriaCosto(models.Model):
    codice = models.CharField(max_length=20, unique=True)
    descrizione = models.CharField(max_length=255)
    attiva = models.BooleanField(default=True)
    ordinamento = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f'{self.codice} - {self.descrizione}'

    class Meta:
        ordering = ['ordinamento', 'codice']
        verbose_name = 'Categoria costo'
        verbose_name_plural = 'Categorie costo'
