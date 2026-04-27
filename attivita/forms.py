from django import forms

from .models import Attivita


class AttivitaForm(forms.ModelForm):
    class Meta:
        model = Attivita
        fields = ['cliente', 'opportunita', 'tipo', 'titolo', 'descrizione',
                  'data', 'durata_minuti', 'completata', 'owner']
        widgets = {
            'cliente': forms.Select(attrs={'class': 'select'}),
            'opportunita': forms.Select(attrs={'class': 'select'}),
            'tipo': forms.Select(attrs={'class': 'select'}),
            'titolo': forms.TextInput(attrs={'class': 'input', 'placeholder': 'Es. Call di follow-up'}),
            'descrizione': forms.Textarea(attrs={'class': 'textarea', 'rows': 3}),
            'data': forms.DateTimeInput(attrs={'class': 'input', 'type': 'datetime-local'}),
            'durata_minuti': forms.NumberInput(attrs={'class': 'input', 'min': 5, 'step': 5}),
            'owner': forms.Select(attrs={'class': 'select'}),
        }
