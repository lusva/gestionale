from django import forms

from .models import Opportunita


class OpportunitaForm(forms.ModelForm):
    class Meta:
        model = Opportunita
        fields = ['cliente', 'titolo', 'descrizione', 'valore', 'stadio',
                  'probabilita', 'chiusura_prevista', 'owner']
        widgets = {
            'cliente': forms.Select(attrs={'class': 'select'}),
            'titolo': forms.TextInput(attrs={'class': 'input', 'placeholder': 'Es. Audit software gestione cantieri'}),
            'descrizione': forms.Textarea(attrs={'class': 'textarea', 'rows': 3}),
            'valore': forms.NumberInput(attrs={'class': 'input mono', 'step': '0.01', 'min': '0'}),
            'stadio': forms.Select(attrs={'class': 'select'}),
            'probabilita': forms.NumberInput(attrs={'class': 'input', 'min': 0, 'max': 100}),
            'chiusura_prevista': forms.DateInput(attrs={'class': 'input', 'type': 'date'}),
            'owner': forms.Select(attrs={'class': 'select'}),
        }
