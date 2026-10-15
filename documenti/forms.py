from django.utils.timezone import localdate

from django import forms
from django.forms import inlineformset_factory

from .models import (
    FatturaRicorrente,
    RigaDocumento,
    RigaFatturaAcquisto,
    RigaFatturaRicorrente,
    ScadenzaFattura,
    ScadenzaFatturaAcquisto,
    TestataFattura,
    TestataFatturaAcquisto,
)


class DefaultDateMixin:
    """Imposta come default ``oggi`` su ``data_documento`` e
    ``data_registrazione`` sia in fase di rendering (per i form non bound)
    sia in fase di validazione (se l'utente svuota il campo).

    Nelle form di update non sovrascrive il valore esistente.
    """
    DEFAULT_DATE_FIELDS = ('data_documento', 'data_registrazione')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = localdate()
        instance = getattr(self, 'instance', None)
        is_new = not (instance and instance.pk)
        for name in self.DEFAULT_DATE_FIELDS:
            if name not in self.fields:
                continue
            if is_new:
                self.initial[name] = today
            elif not self.initial.get(name):
                instance_val = getattr(instance, name, None) if instance else None
                if not instance_val:
                    self.initial[name] = today

    def clean(self):
        cleaned = super().clean()
        today = localdate()
        for name in self.DEFAULT_DATE_FIELDS:
            if name in self.fields and not cleaned.get(name):
                cleaned[name] = today
        return cleaned


class SelectConDati(forms.Select):
    """Select di oggetti che porta su ogni <option> alcuni attributi
    ``data-*`` presi dall'oggetto (``dati`` = {attributo: campo}): il form li
    usa per precompilare altri campi senza chiederli al server (es. la forma
    di pagamento del cliente scelto)."""

    def __init__(self, attrs=None, dati=None):
        super().__init__(attrs)
        self.dati = dati or {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        istanza = getattr(value, 'instance', None)
        if istanza is not None:
            for attributo, campo in self.dati.items():
                valore = getattr(istanza, campo, None)
                if valore not in (None, ''):
                    option['attrs'][attributo] = valore
        return option


class ContiAziendaMixin:
    """Conto corrente dei documenti al cliente: i conti dell'azienda (dove il
    cliente paga), non quelli di clienti e fornitori; si tiene quello già
    scelto. Nei documenti nuovi propone il conto predefinito dell'azienda."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'conto_corrente' not in self.fields:
            return
        from anagrafiche.models import ContoCorrente
        conti = ContoCorrente.objects.filter(anagrafica_azienda__isnull=False)
        if self.instance.conto_corrente_id:
            conti = conti | ContoCorrente.objects.filter(pk=self.instance.conto_corrente_id)
        self.fields['conto_corrente'].queryset = conti.order_by('-default', 'banca')
        if not self.instance.pk and not self.is_bound:
            predefinito = conti.filter(default=True).first() or conti.first()
            if predefinito is not None:
                self.initial.setdefault('conto_corrente', predefinito.pk)
        if 'cliente' in self.fields:
            self.fields['cliente'].queryset = self.fields['cliente'].queryset.order_by('ragione_sociale')


# forma di pagamento e IVA predefinite del cliente, lette dal form documento
CLIENTE_CON_PREDEFINITI = {'data-forma': 'forme_pagamento_id', 'data-iva': 'posizione_iva_id'}


class SezionaleBloccatoMixin:
    """Il sezionale fa parte della numerazione (anno, sezionale, numero):
    su un documento già salvato non si cambia, altrimenti il numero finirebbe
    nel progressivo di un altro sezionale (doppione o buco)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = getattr(self, 'instance', None)
        if instance is not None and instance.pk and 'sezionale' in self.fields:
            self.fields['sezionale'].disabled = True


# ---------------------------------------------------------------------------
# Fattura cliente (testata + righe + scadenze)
# ---------------------------------------------------------------------------


class TestataFatturaForm(ContiAziendaMixin, SezionaleBloccatoMixin, DefaultDateMixin, forms.ModelForm):
    class Meta:
        model = TestataFattura
        fields = [
            'tipo_documento',
            'sezionale',
            'data_documento',
            'data_registrazione',
            'cliente',
            'forme_pagamento',
            'conto_corrente',
            'spese_imballo',
            'spese_trasporto',
            'sconto',
            'sconto_incondizionato',
            'pagata',
            'data_pagamento',
            'note',
        ]
        widgets = {
            'tipo_documento': forms.Select(attrs={'class': 'select'}),
            'sezionale': forms.TextInput(
                attrs={'class': 'input mono', 'maxlength': 10, 'placeholder': '(opzionale)'},
            ),
            'data_documento': forms.DateInput(
                attrs={'class': 'input', 'type': 'date'}, format='%Y-%m-%d',
            ),
            'data_registrazione': forms.DateInput(
                attrs={'class': 'input', 'type': 'date'}, format='%Y-%m-%d',
            ),
            'cliente': SelectConDati(attrs={'class': 'select', 'data-cerca': ''},
                                     dati=CLIENTE_CON_PREDEFINITI),
            'forme_pagamento': forms.Select(attrs={'class': 'select'}),
            'conto_corrente': forms.Select(attrs={'class': 'select'}),
            'spese_imballo': forms.NumberInput(
                attrs={'class': 'input mono', 'step': '0.01'},
            ),
            'spese_trasporto': forms.NumberInput(
                attrs={'class': 'input mono', 'step': '0.01'},
            ),
            'sconto': forms.NumberInput(
                attrs={'class': 'input mono', 'step': '0.01'},
            ),
            'sconto_incondizionato': forms.NumberInput(
                attrs={'class': 'input mono', 'step': '0.01'},
            ),
            'data_pagamento': forms.DateInput(
                attrs={'class': 'input', 'type': 'date'}, format='%Y-%m-%d',
            ),
            'note': forms.Textarea(
                attrs={'class': 'textarea', 'rows': 3},
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk and not self.is_bound:
            # Le fatture nuove sono immediate (TD01), come nell'API: il TD24
            # predefinito del modello è per le differite da DDT.
            self.initial.setdefault('tipo_documento', TestataFattura.TipoDocumento.TD01)
        # con incassi registrati «pagata» e la data li calcola il gestionale
        # dagli incassi (dettaglio fattura), non si scrivono qui
        from .servizi import ha_incassi
        self.con_incassi = ha_incassi(self.instance)
        if self.con_incassi:
            self.fields['pagata'].disabled = True
            self.fields['data_pagamento'].disabled = True

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('cliente'):
            raise forms.ValidationError(
                'Devi indicare un cliente per la fattura.'
            )
        return cleaned


def _zero(testo: str) -> bool:
    from decimal import Decimal, InvalidOperation
    try:
        return not testo or Decimal(testo.replace(',', '.')) == 0
    except InvalidOperation:
        return False


class RigaDocumentoForm(forms.ModelForm):
    class Meta:
        model = RigaDocumento
        fields = [
            'numero_riga',
            'articolo',
            'descrizione_libera',
            'quantita',
            'um',
            'importo_unitario',
            'iva',
        ]
        # le larghezze le dà la tabella delle righe (.righe-tbl in app.css)
        widgets = {
            'numero_riga': forms.NumberInput(attrs={'class': 'input mono'}),
            'articolo': forms.Select(attrs={'class': 'select'}),
            'descrizione_libera': forms.TextInput(
                attrs={'class': 'input', 'placeholder': 'Descrizione'},
            ),
            'quantita': forms.NumberInput(
                attrs={'class': 'input mono', 'step': '0.01', 'inputmode': 'decimal'},
            ),
            'um': forms.Select(attrs={'class': 'select'}),
            'importo_unitario': forms.NumberInput(
                attrs={'class': 'input mono', 'step': '0.01', 'inputmode': 'decimal'},
            ),
            'iva': forms.Select(attrs={'class': 'select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['articolo'].empty_label = 'Articolo (facoltativo)'
        self.fields['iva'].empty_label = 'IVA…'
        self.fields['um'].choices = [('', '—')] + [c for c in self.fields['um'].choices if c[0]]

    def has_changed(self):
        """Una riga nuova senza articolo, descrizione né prezzo è vuota anche
        se ha la quantità o l'IVA proposte dal form: non si salva (come la
        riga vuota in fondo al formset)."""
        cambiata = super().has_changed()
        if not cambiata or self.instance.pk:
            return cambiata
        valore = lambda campo: (self[campo].data or '').strip()
        return bool(valore('articolo') or valore('descrizione_libera')
                    or not _zero(valore('importo_unitario')))


# inline formset: righe figlie di una TestataFattura
RigaFatturaFormSet = inlineformset_factory(
    parent_model=TestataFattura,
    model=RigaDocumento,
    form=RigaDocumentoForm,
    fk_name='testata_fattura',
    extra=1,
    can_delete=True,
)


class ScadenzaFatturaForm(forms.ModelForm):
    class Meta:
        model = ScadenzaFattura
        fields = ['data', 'importo']
        widgets = {
            'data': forms.DateInput(attrs={'class': 'input', 'type': 'date'}, format='%Y-%m-%d'),
            'importo': forms.NumberInput(
                attrs={'class': 'input mono', 'step': '0.01'},
            ),
        }


ScadenzeFatturaFormSet = inlineformset_factory(
    parent_model=TestataFattura,
    model=ScadenzaFattura,
    form=ScadenzaFatturaForm,
    fk_name='fattura',
    extra=1,
    can_delete=True,
)


# ---------------------------------------------------------------------------
# Fattura di acquisto inserita a mano (cartacea, fornitore estero…)
# ---------------------------------------------------------------------------

# I tipi che si ricevono da un fornitore; le integrazioni e le autofatture
# (TD16-TD27) le emette l'azienda, non si registrano qui.
TIPI_ACQUISTO_A_MANO = ('TD01', 'TD04', 'TD05', 'TD06', 'TD24', 'TD25')
ESTENSIONI_ALLEGATO = ('.pdf', '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.heic', '.webp')
MAX_ALLEGATO = 20 * 1024 * 1024


def _data_widget():
    return forms.DateInput(attrs={'class': 'input', 'type': 'date'}, format='%Y-%m-%d')


class AllegatoMixin:
    def clean_allegato(self):
        file = self.cleaned_data.get('allegato')
        if file and hasattr(file, 'size'):
            if not file.name.lower().endswith(ESTENSIONI_ALLEGATO):
                raise forms.ValidationError('Carica un PDF o un\'immagine (JPG, PNG, TIFF, HEIC).')
            if file.size > MAX_ALLEGATO:
                raise forms.ValidationError('File troppo grande (massimo 20 MB).')
        return file


class TestataFatturaAcquistoForm(AllegatoMixin, DefaultDateMixin, forms.ModelForm):
    DEFAULT_DATE_FIELDS = ('data_registrazione',)

    class Meta:
        model = TestataFatturaAcquisto
        fields = [
            'fornitore', 'numero_fornitore', 'data_fornitore', 'tipo_documento',
            'data_registrazione', 'stato', 'forme_pagamento', 'note', 'allegato',
        ]
        labels = {
            'numero_fornitore': 'Numero della fattura',
            'data_fornitore': 'Data della fattura',
            'data_registrazione': 'Data di registrazione',
            'forme_pagamento': 'Forma di pagamento',
            'allegato': 'PDF o scansione',
        }
        widgets = {
            'fornitore': SelectConDati(attrs={'class': 'select', 'data-cerca': ''},
                                       dati={'data-forma': 'forme_pagamento_id'}),
            'numero_fornitore': forms.TextInput(attrs={'class': 'input mono', 'autocomplete': 'off'}),
            'data_fornitore': _data_widget(),
            'tipo_documento': forms.Select(attrs={'class': 'select'}),
            'data_registrazione': _data_widget(),
            'stato': forms.Select(attrs={'class': 'select'}),
            'forme_pagamento': forms.Select(attrs={'class': 'select'}),
            'note': forms.Textarea(attrs={'class': 'textarea', 'rows': 3}),
            'allegato': forms.ClearableFileInput(attrs={'class': 'input', 'accept': '.pdf,image/*'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from anagrafiche.models import Fornitore
        fornitori = Fornitore.objects.filter(soft_delete=False)
        if self.instance.fornitore_id:
            fornitori = fornitori | Fornitore.objects.filter(pk=self.instance.fornitore_id)
        self.fields['fornitore'].queryset = fornitori.order_by('ragione_sociale', 'cognome')
        self.fields['fornitore'].required = True
        self.fields['numero_fornitore'].required = True
        self.fields['data_fornitore'].required = True
        tipi = set(TIPI_ACQUISTO_A_MANO) | {self.instance.tipo_documento}
        self.fields['tipo_documento'].choices = [
            c for c in self.fields['tipo_documento'].choices if c[0] in tipi]
        if not self.instance.pk and not self.is_bound:
            # inserita a mano = l'ha controllata chi la scrive: niente bozza
            self.initial.setdefault('stato', TestataFatturaAcquisto.StatoFattura.CONFERMATA)
            self.initial.setdefault('tipo_documento', TestataFatturaAcquisto.TipoDocumento.TD01)

    def clean_numero_fornitore(self):
        return (self.cleaned_data.get('numero_fornitore') or '').strip()

    def clean(self):
        dati = super().clean()
        from django.urls import reverse
        from django.utils.html import format_html
        fornitore, numero, data = (dati.get('fornitore'), dati.get('numero_fornitore'),
                                   dati.get('data_fornitore'))
        if fornitore and numero and data:
            doppia = (TestataFatturaAcquisto.objects
                      .filter(fornitore=fornitore, numero_fornitore=numero, data_fornitore=data)
                      .exclude(pk=self.instance.pk).first())
            if doppia is not None:
                self.add_error('numero_fornitore', format_html(
                    'Questa fattura è già registrata: <a href="{}">{}/{}</a>.',
                    reverse('documenti:fattura_acquisto_detail', args=[doppia.pk]),
                    doppia.numero, doppia.anno))
        return dati

    def save(self, commit=True):
        # la data del documento è quella della fattura ricevuta: dà l'anno del
        # protocollo interno e la base delle scadenze, come per l'import XML
        self.instance.data_documento = self.cleaned_data.get('data_fornitore')
        return super().save(commit)


class TestataFatturaAcquistoXmlForm(AllegatoMixin, forms.ModelForm):
    """Fattura importata da XML: importi e righe fanno fede all'XML, si
    cambiano solo i dati interni."""

    class Meta:
        model = TestataFatturaAcquisto
        fields = ['stato', 'data_registrazione', 'note', 'allegato']
        labels = TestataFatturaAcquistoForm.Meta.labels
        widgets = {
            'stato': forms.Select(attrs={'class': 'select'}),
            'data_registrazione': _data_widget(),
            'note': forms.Textarea(attrs={'class': 'textarea', 'rows': 3}),
            'allegato': forms.ClearableFileInput(attrs={'class': 'input', 'accept': '.pdf,image/*'}),
        }


class RigaFatturaAcquistoForm(forms.ModelForm):
    class Meta:
        model = RigaFatturaAcquisto
        fields = ['numero_riga', 'descrizione', 'quantita', 'um', 'prezzo_unitario',
                  'sconto_percentuale', 'aliquota_iva', 'categoria_costo']
        widgets = {
            'numero_riga': forms.NumberInput(attrs={'class': 'input mono'}),
            'descrizione': forms.TextInput(attrs={'class': 'input', 'placeholder': 'Descrizione'}),
            'quantita': forms.NumberInput(attrs={'class': 'input mono', 'step': '0.01', 'inputmode': 'decimal'}),
            'um': forms.Select(attrs={'class': 'select'}),
            'prezzo_unitario': forms.NumberInput(attrs={'class': 'input mono', 'step': '0.0001', 'inputmode': 'decimal'}),
            'sconto_percentuale': forms.NumberInput(attrs={'class': 'input mono', 'step': '0.01', 'inputmode': 'decimal', 'placeholder': 'sconto %'}),
            'aliquota_iva': forms.Select(attrs={'class': 'select'}),
            'categoria_costo': forms.Select(attrs={'class': 'select', 'data-no-cerca': ''}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['aliquota_iva'].empty_label = 'IVA…'
        self.fields['aliquota_iva'].required = True
        self.fields['categoria_costo'].empty_label = 'Categoria di costo…'
        self.fields['categoria_costo'].queryset = _categorie_attive(self.instance.categoria_costo_id)
        self.fields['um'].choices = [('', '—')] + [c for c in self.fields['um'].choices if c[0]]

    def has_changed(self):
        """Come RigaDocumentoForm: una riga nuova con solo quantità, IVA o
        categoria proposte è vuota e non si salva."""
        cambiata = super().has_changed()
        if not cambiata or self.instance.pk:
            return cambiata
        valore = lambda campo: (self[campo].data or '').strip()
        return bool(valore('descrizione') or not _zero(valore('prezzo_unitario')))


def _categorie_attive(tenere=None):
    from django.db.models import Q
    from anagrafiche.models import CategoriaCosto
    filtro = Q(attiva=True)
    if tenere:
        filtro |= Q(pk=tenere)
    return CategoriaCosto.objects.filter(filtro).order_by('ordinamento', 'descrizione')


class ScadenzaFatturaAcquistoForm(forms.ModelForm):
    class Meta:
        model = ScadenzaFatturaAcquisto
        fields = ['data_scadenza', 'importo', 'modalita_pagamento', 'iban']
        widgets = {
            'data_scadenza': _data_widget(),
            'importo': forms.NumberInput(attrs={'class': 'input mono', 'step': '0.01'}),
            'modalita_pagamento': forms.Select(attrs={'class': 'select', 'data-no-cerca': ''}),
            'iban': forms.TextInput(attrs={'class': 'input mono', 'placeholder': 'IBAN del fornitore'}),
        }


class CategoriaRigaForm(forms.ModelForm):
    """Solo la categoria di costo, per le righe delle fatture da XML."""

    class Meta:
        model = RigaFatturaAcquisto
        fields = ['categoria_costo']
        widgets = {'categoria_costo': forms.Select(attrs={'class': 'select', 'data-no-cerca': ''})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['categoria_costo'].empty_label = 'Non categorizzata'
        self.fields['categoria_costo'].queryset = _categorie_attive(self.instance.categoria_costo_id)


RigaFatturaAcquistoFormSet = inlineformset_factory(
    TestataFatturaAcquisto, RigaFatturaAcquisto, form=RigaFatturaAcquistoForm,
    fk_name='testata', extra=1, can_delete=True,
)
ScadenzeAcquistoFormSet = inlineformset_factory(
    TestataFatturaAcquisto, ScadenzaFatturaAcquisto, form=ScadenzaFatturaAcquistoForm,
    fk_name='fattura', extra=1, can_delete=True,
)
CategorieRigheFormSet = inlineformset_factory(
    TestataFatturaAcquisto, RigaFatturaAcquisto, form=CategoriaRigaForm,
    fk_name='testata', extra=0, can_delete=False,
)


# ---------------------------------------------------------------------------
# Fattura ricorrente (modello + righe)
# ---------------------------------------------------------------------------


class FatturaRicorrenteForm(ContiAziendaMixin, forms.ModelForm):
    class Meta:
        model = FatturaRicorrente
        fields = [
            'nome', 'cliente', 'periodicita', 'giorno_emissione', 'data_inizio', 'data_fine',
            'competenza', 'tipo_documento', 'sezionale', 'forme_pagamento', 'conto_corrente',
            'note', 'invia_sdi', 'invia_email',
        ]
        labels = {
            'giorno_emissione': 'Giorno di emissione', 'data_inizio': 'Prima emissione dal',
            'data_fine': 'Ultima emissione entro il', 'competenza': 'Si fattura',
            'forme_pagamento': 'Forma di pagamento', 'conto_corrente': 'Conto corrente (IBAN in fattura)',
            'note': 'Note in fattura', 'invia_sdi': 'Invia subito al SDI',
            'invia_email': 'Invia per email al cliente',
        }
        widgets = {
            'nome': forms.TextInput(attrs={'class': 'input', 'placeholder': 'Canone assistenza…'}),
            'cliente': SelectConDati(attrs={'class': 'select', 'data-cerca': ''},
                                     dati=CLIENTE_CON_PREDEFINITI),
            'periodicita': forms.Select(attrs={'class': 'select'}),
            'giorno_emissione': forms.NumberInput(attrs={'class': 'input mono', 'min': 1, 'max': 31}),
            'data_inizio': forms.DateInput(attrs={'class': 'input', 'type': 'date'}, format='%Y-%m-%d'),
            'data_fine': forms.DateInput(attrs={'class': 'input', 'type': 'date'}, format='%Y-%m-%d'),
            'competenza': forms.Select(attrs={'class': 'select'}),
            'tipo_documento': forms.Select(attrs={'class': 'select'}),
            'sezionale': forms.TextInput(attrs={'class': 'input mono', 'maxlength': 10,
                                                'placeholder': '(opzionale)'}),
            'forme_pagamento': forms.Select(attrs={'class': 'select'}),
            'conto_corrente': forms.Select(attrs={'class': 'select'}),
            'note': forms.Textarea(attrs={'class': 'textarea', 'rows': 3}),
        }
        help_texts = {
            'giorno_emissione': '31 = l\'ultimo giorno del mese (28 o 29 a febbraio).',
            'forme_pagamento': 'Vuota = quella del cliente.',
        }

    def clean(self):
        dati = super().clean()
        inizio, fine = dati.get('data_inizio'), dati.get('data_fine')
        if inizio and fine and fine < inizio:
            self.add_error('data_fine', 'Viene prima della prima emissione.')
        return dati


class RigaFatturaRicorrenteForm(RigaDocumentoForm):
    class Meta(RigaDocumentoForm.Meta):
        model = RigaFatturaRicorrente

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['iva'].empty_label = 'IVA del cliente'
        self.fields['descrizione_libera'].widget.attrs['placeholder'] = 'Descrizione, es. Canone {mese} {anno}'


RigaFatturaRicorrenteFormSet = inlineformset_factory(
    FatturaRicorrente, RigaFatturaRicorrente, form=RigaFatturaRicorrenteForm,
    fk_name='ricorrenza', extra=1, can_delete=True,
)

