"""Form per il modulo Cashflow."""
from __future__ import annotations

from django import forms

from .models import ScadenzaFiscale, ScadenzaSpesa, SpesaRicorrente


SELECT = forms.Select(attrs={'class': 'select'})
TEXT = forms.TextInput(attrs={'class': 'input'})
DEC = forms.NumberInput(attrs={'class': 'input mono', 'step': '0.01'})
INT = forms.NumberInput(attrs={'class': 'input mono', 'step': '1', 'min': '1', 'max': '31'})
DATE = forms.DateInput(attrs={'class': 'input', 'type': 'date'})
TEXTAREA = forms.Textarea(attrs={'class': 'textarea', 'rows': 3})


class ScadenzaFiscaleForm(forms.ModelForm):
    class Meta:
        model = ScadenzaFiscale
        fields = [
            'data_scadenza', 'tipo', 'descrizione', 'importo',
            'pagata', 'data_pagamento', 'note',
        ]
        widgets = {
            'data_scadenza': DATE,
            'tipo': SELECT,
            'descrizione': forms.TextInput(
                attrs={'class': 'input', 'placeholder': 'Es. Liquidazione IVA aprile 2026'},
            ),
            'importo': DEC,
            'data_pagamento': DATE,
            'note': TEXTAREA,
        }


class SpesaRicorrenteForm(forms.ModelForm):
    class Meta:
        model = SpesaRicorrente
        fields = [
            'descrizione', 'importo', 'periodicita', 'giorno_del_mese',
            'data_inizio', 'data_fine', 'categoria_costo', 'fornitore',
            'attiva', 'note',
        ]
        widgets = {
            'descrizione': forms.TextInput(
                attrs={'class': 'input', 'placeholder': 'Es. Affitto ufficio'},
            ),
            'importo': DEC,
            'periodicita': SELECT,
            'giorno_del_mese': INT,
            'data_inizio': DATE,
            'data_fine': DATE,
            'categoria_costo': SELECT,
            'fornitore': SELECT,
            'note': TEXTAREA,
        }


class ScadenzaSpesaForm(forms.ModelForm):
    class Meta:
        model = ScadenzaSpesa
        fields = ['data_scadenza', 'importo', 'pagata', 'data_pagamento', 'note']
        widgets = {
            'data_scadenza': DATE,
            'importo': DEC,
            'data_pagamento': DATE,
            'note': TEXTAREA,
        }
