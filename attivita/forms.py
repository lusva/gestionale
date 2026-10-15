from django import forms
from accounts.tenancy import utenti_azienda

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
            'data': forms.DateTimeInput(attrs={'class': 'input', 'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'durata_minuti': forms.NumberInput(attrs={'class': 'input', 'min': 5, 'step': 5}),
            'owner': forms.Select(attrs={'class': 'select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['owner'].queryset = utenti_azienda()
        # l'etichetta di un'opportunità contiene il cliente: caricato insieme
        self.fields['opportunita'].queryset = (
            self.fields['opportunita'].queryset.select_related('cliente'))
        self.fields['owner'].label = 'Assegnatario'
        self.fields['data'].label = 'Data e ora'
        self.fields['opportunita'].help_text = (
            'Se scegli un’opportunità, il cliente viene collegato automaticamente.')

    def clean(self):
        cleaned_data = super().clean()
        cliente = cleaned_data.get('cliente')
        opportunita = cleaned_data.get('opportunita')
        if opportunita:
            if cliente and cliente.pk != opportunita.cliente_id:
                self.add_error(
                    'opportunita',
                    'L’opportunità selezionata appartiene a un altro cliente.',
                )
            elif not cliente:
                cleaned_data['cliente'] = opportunita.cliente
        return cleaned_data
