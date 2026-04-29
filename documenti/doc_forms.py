"""
Form e formset per Offerta, Ordine, DDT, NotaCredito, DdtFornitore.

Pattern: una ModelForm per la testata + un inline formset di RigaDocumento.
La fattura cliente ha il suo modulo dedicato (``forms.py``) perché ha le
scadenze.
"""
from django import forms

from .doc_generic import make_riga_formset
from .forms import DefaultDateMixin, RigaDocumentoForm
from .models import (
    TestataDdt,
    TestataDdtFornitore,
    TestataNotaCredito,
    TestataOfferta,
    TestataOrdine,
)


# Widget riusabili per le date e i campi monetari
DATE_INPUT = forms.DateInput(attrs={'class': 'input', 'type': 'date'})
SELECT = forms.Select(attrs={'class': 'select'})
TEXT = forms.TextInput(attrs={'class': 'input'})
DEC = forms.NumberInput(attrs={'class': 'input mono', 'step': '0.01'})
INT = forms.NumberInput(attrs={'class': 'input mono', 'step': '1'})
TEXTAREA = forms.Textarea(attrs={'class': 'textarea', 'rows': 3})


# ---------------------------------------------------------------------------
# Offerta
# ---------------------------------------------------------------------------


class TestataOffertaForm(DefaultDateMixin, forms.ModelForm):
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
            'cliente': SELECT, 'forme_pagamento': SELECT,
            'conto_corrente': SELECT, 'stato': SELECT,
            'spese_imballo': DEC, 'spese_trasporto': DEC,
            'sconto': DEC, 'sconto_incondizionato': DEC,
            'note': TEXTAREA,
        }


RigaOffertaFormSet = make_riga_formset(TestataOfferta, 'testata_offerta', RigaDocumentoForm)


# ---------------------------------------------------------------------------
# Ordine
# ---------------------------------------------------------------------------


class TestataOrdineForm(DefaultDateMixin, forms.ModelForm):
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
            'cliente': SELECT, 'forme_pagamento': SELECT,
            'conto_corrente': SELECT, 'stato': SELECT,
            'spese_imballo': DEC, 'spese_trasporto': DEC,
            'sconto': DEC, 'sconto_incondizionato': DEC,
            'note': TEXTAREA,
        }


RigaOrdineFormSet = make_riga_formset(TestataOrdine, 'testata_ordine', RigaDocumentoForm)


# ---------------------------------------------------------------------------
# DDT cliente
# ---------------------------------------------------------------------------


class TestataDdtForm(DefaultDateMixin, forms.ModelForm):
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
                attrs={'class': 'input', 'type': 'datetime-local'},
            ),
            'cliente': SELECT, 'forme_pagamento': SELECT,
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


class TestataNotaCreditoForm(DefaultDateMixin, forms.ModelForm):
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
            'cliente': SELECT, 'forme_pagamento': SELECT,
            'conto_corrente': SELECT, 'tipo_documento': SELECT,
            'sezionale': forms.TextInput(
                attrs={'class': 'input mono', 'maxlength': 10, 'placeholder': '(opzionale)'},
            ),
            'spese_imballo': DEC, 'spese_trasporto': DEC,
            'sconto': DEC, 'sconto_incondizionato': DEC,
            'note': TEXTAREA,
        }


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
            'fornitore': SELECT, 'forme_pagamento': SELECT,
            'conto_corrente': SELECT,
            'numero_palette': INT,
            'peso_netto': DEC, 'peso_lordo': DEC,
            'vettore': TEXT,
            'note': TEXTAREA,
        }


RigaDdtFornitoreFormSet = make_riga_formset(
    TestataDdtFornitore, 'testata_ddt_fornitore', RigaDocumentoForm,
)
