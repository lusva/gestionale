from django.utils.timezone import localdate

from django import forms
from django.forms import inlineformset_factory

from .models import (
    RigaDocumento,
    ScadenzaFattura,
    TestataFattura,
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


# ---------------------------------------------------------------------------
# Fattura cliente (testata + righe + scadenze)
# ---------------------------------------------------------------------------


class TestataFatturaForm(DefaultDateMixin, forms.ModelForm):
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
                attrs={'class': 'input', 'type': 'date'},
            ),
            'data_registrazione': forms.DateInput(
                attrs={'class': 'input', 'type': 'date'},
            ),
            'cliente': forms.Select(attrs={'class': 'select'}),
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
                attrs={'class': 'input', 'type': 'date'},
            ),
            'note': forms.Textarea(
                attrs={'class': 'textarea', 'rows': 3},
            ),
        }

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get('cliente'):
            raise forms.ValidationError(
                'Devi indicare un cliente per la fattura.'
            )
        return cleaned


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
        widgets = {
            'numero_riga': forms.NumberInput(
                attrs={'class': 'input mono', 'style': 'width: 56px;'},
            ),
            'articolo': forms.Select(attrs={'class': 'select'}),
            'descrizione_libera': forms.TextInput(
                attrs={'class': 'input', 'placeholder': 'Descrizione'},
            ),
            'quantita': forms.NumberInput(
                attrs={'class': 'input mono', 'step': '0.01', 'style': 'width: 90px;'},
            ),
            'um': forms.Select(attrs={'class': 'select', 'style': 'width: 80px;'}),
            'importo_unitario': forms.NumberInput(
                attrs={'class': 'input mono', 'step': '0.01', 'style': 'width: 110px;'},
            ),
            'iva': forms.Select(attrs={'class': 'select'}),
        }


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
            'data': forms.DateInput(attrs={'class': 'input', 'type': 'date'}),
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
