"""
Form e formset per Offerta, Ordine, DDT, NotaCredito, DdtFornitore.

Pattern: una ModelForm per la testata + un inline formset di RigaDocumento.
La fattura cliente ha il suo modulo dedicato (``forms.py``) perché ha le
scadenze.
"""
from django import forms

from .doc_generic import make_riga_formset
from .forms import (
    CLIENTE_CON_PREDEFINITI, ContiAziendaMixin, DefaultDateMixin, RigaDocumentoForm,
    SelectConDati, SezionaleBloccatoMixin,
)
from .models import (
    TestataDdt,
    TestataDdtFornitore,
    TestataFattura,
    TestataNotaCredito,
    TestataOfferta,
    TestataOrdine,
)


# Widget riusabili per le date e i campi monetari
DATE_INPUT = forms.DateInput(attrs={'class': 'input', 'type': 'date'}, format='%Y-%m-%d')
SELECT = forms.Select(attrs={'class': 'select'})
CLIENTE = SelectConDati(attrs={'class': 'select', 'data-cerca': ''}, dati=CLIENTE_CON_PREDEFINITI)
FORNITORE = forms.Select(attrs={'class': 'select', 'data-cerca': ''})
TEXT = forms.TextInput(attrs={'class': 'input'})
DEC = forms.NumberInput(attrs={'class': 'input mono', 'step': '0.01'})
INT = forms.NumberInput(attrs={'class': 'input mono', 'step': '1'})
TEXTAREA = forms.Textarea(attrs={'class': 'textarea', 'rows': 3})


# ---------------------------------------------------------------------------
# Offerta
# ---------------------------------------------------------------------------


class TestataOffertaForm(ContiAziendaMixin, DefaultDateMixin, forms.ModelForm):
    class Meta:
        model = TestataOfferta
        fields = [
            'data_documento', 'data_registrazione', 'cliente',
            'forme_pagamento', 'conto_corrente',
            'scadenza', 'stato',
            'spese_imballo', 'spese_trasporto',
            'sconto', 'sconto_incondizionato', 'note',
        ]
        widgets = {
            'data_documento': DATE_INPUT,
            'data_registrazione': DATE_INPUT,
            'scadenza': DATE_INPUT,
            'cliente': CLIENTE, 'forme_pagamento': SELECT,
            'conto_corrente': SELECT, 'stato': SELECT,
            'spese_imballo': DEC, 'spese_trasporto': DEC,
            'sconto': DEC, 'sconto_incondizionato': DEC,
            'note': TEXTAREA,
        }


RigaOffertaFormSet = make_riga_formset(TestataOfferta, 'testata_offerta', RigaDocumentoForm)


# ---------------------------------------------------------------------------
# Ordine
# ---------------------------------------------------------------------------


class TestataOrdineForm(ContiAziendaMixin, DefaultDateMixin, forms.ModelForm):
    class Meta:
        model = TestataOrdine
        fields = [
            'data_documento', 'data_registrazione', 'cliente',
            'forme_pagamento', 'conto_corrente',
            'stato',
            'spese_imballo', 'spese_trasporto',
            'sconto', 'sconto_incondizionato', 'note',
        ]
        widgets = {
            'data_documento': DATE_INPUT,
            'data_registrazione': DATE_INPUT,
            'cliente': CLIENTE, 'forme_pagamento': SELECT,
            'conto_corrente': SELECT, 'stato': SELECT,
            'spese_imballo': DEC, 'spese_trasporto': DEC,
            'sconto': DEC, 'sconto_incondizionato': DEC,
            'note': TEXTAREA,
        }


RigaOrdineFormSet = make_riga_formset(TestataOrdine, 'testata_ordine', RigaDocumentoForm)


# ---------------------------------------------------------------------------
# DDT cliente
# ---------------------------------------------------------------------------


class TestataDdtForm(ContiAziendaMixin, DefaultDateMixin, forms.ModelForm):
    class Meta:
        model = TestataDdt
        fields = [
            'data_documento', 'data_registrazione', 'cliente',
            'forme_pagamento', 'conto_corrente',
            'stato', 'numero_palette', 'peso_netto', 'peso_lordo',
            'cura', 'causale_trasporto', 'porto', 'aspetto_beni',
            'vettore', 'data_ora_ritiro', 'destinazione_merce',
            'note',
        ]
        widgets = {
            'data_documento': DATE_INPUT,
            'data_registrazione': DATE_INPUT,
            'data_ora_ritiro': forms.DateTimeInput(
                attrs={'class': 'input', 'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M',
            ),
            'cliente': CLIENTE, 'forme_pagamento': SELECT,
            'conto_corrente': SELECT, 'stato': SELECT,
            'numero_palette': INT,
            'peso_netto': DEC, 'peso_lordo': DEC,
            'cura': TEXT, 'causale_trasporto': TEXT,
            'porto': TEXT, 'aspetto_beni': TEXT, 'vettore': TEXT,
            'destinazione_merce': SELECT,
            'note': TEXTAREA,
        }


RigaDdtFormSet = make_riga_formset(TestataDdt, 'testata_ddt', RigaDocumentoForm)


# ---------------------------------------------------------------------------
# Nota di credito
# ---------------------------------------------------------------------------


class TestataNotaCreditoForm(ContiAziendaMixin, SezionaleBloccatoMixin, DefaultDateMixin, forms.ModelForm):
    class Meta:
        model = TestataNotaCredito
        fields = [
            'tipo_documento', 'sezionale',
            'data_documento', 'data_registrazione',
            'cliente', 'forme_pagamento', 'conto_corrente',
            'spese_imballo', 'spese_trasporto',
            'sconto', 'sconto_incondizionato', 'note',
        ]
        widgets = {
            'data_documento': DATE_INPUT,
            'data_registrazione': DATE_INPUT,
            'cliente': CLIENTE, 'forme_pagamento': SELECT,
            'conto_corrente': SELECT, 'tipo_documento': SELECT,
            'sezionale': forms.TextInput(
                attrs={'class': 'input mono', 'maxlength': 10, 'placeholder': '(opzionale)'},
            ),
            'spese_imballo': DEC, 'spese_trasporto': DEC,
            'sconto': DEC, 'sconto_incondizionato': DEC,
            'note': TEXTAREA,
        }

    fatture = forms.ModelMultipleChoiceField(
        queryset=TestataFattura.objects.none(), required=False, label='Fatture stornate',
        widget=forms.SelectMultiple(attrs={'class': 'select', 'size': 4, 'data-no-cerca': ''}),
        help_text='Vanno nell\'XML (DatiFattureCollegate) e la nota ne compensa le rate aperte. '
                  'Cmd/Ctrl + clic per sceglierne più di una.',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .fatt_el import numero_documento_sdi
        if not self.instance.pk and not self.is_bound:
            self.initial.setdefault('sezionale', TestataNotaCredito.SEZIONALE_PREDEFINITO)
        fatture = TestataFattura.objects.select_related('cliente').order_by('-anno', '-numero')
        if self.instance.cliente_id:
            fatture = fatture.filter(cliente_id=self.instance.cliente_id)
        else:
            fatture = fatture.filter(pk__in=fatture.values('pk')[:500])
        if self.instance.pk:
            self.initial.setdefault('fatture', list(self.instance.fatture_origine.values_list('pk', flat=True)))
            fatture = fatture | TestataFattura.objects.filter(pk__in=self.initial['fatture'])
        self.fields['fatture'].queryset = fatture.distinct()
        self.fields['fatture'].label_from_instance = lambda f: (
            f'{numero_documento_sdi(f)} del {f.data_documento:%d/%m/%Y} — {f.cliente}'
            if f.data_documento else f'{numero_documento_sdi(f)} — {f.cliente}')

    def clean(self):
        dati = super().clean()
        cliente = dati.get('cliente')
        altre = [f for f in dati.get('fatture') or [] if cliente and f.cliente_id != cliente.pk]
        if altre:
            self.add_error('fatture', 'Si possono stornare solo fatture dello stesso cliente.')
        return dati

    def save(self, commit=True):
        nota = super().save(commit)
        if commit:
            nota.fatture_origine.set(self.cleaned_data.get('fatture') or [])
        return nota


RigaNotaCreditoFormSet = make_riga_formset(
    TestataNotaCredito, 'testata_nota_credito', RigaDocumentoForm,
)


# ---------------------------------------------------------------------------
# DDT fornitore (ciclo passivo)
# ---------------------------------------------------------------------------


class TestataDdtFornitoreForm(DefaultDateMixin, forms.ModelForm):
    class Meta:
        model = TestataDdtFornitore
        fields = [
            'data_documento', 'data_registrazione', 'fornitore',
            'forme_pagamento', 'conto_corrente',
            'numero_palette', 'peso_netto', 'peso_lordo', 'vettore',
            'note',
        ]
        widgets = {
            'data_documento': DATE_INPUT,
            'data_registrazione': DATE_INPUT,
            'fornitore': FORNITORE, 'forme_pagamento': SELECT,
            'conto_corrente': SELECT,
            'numero_palette': INT,
            'peso_netto': DEC, 'peso_lordo': DEC,
            'vettore': TEXT,
            'note': TEXTAREA,
        }


RigaDdtFornitoreFormSet = make_riga_formset(
    TestataDdtFornitore, 'testata_ddt_fornitore', RigaDocumentoForm,
)
