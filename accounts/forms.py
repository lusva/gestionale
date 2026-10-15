from django import forms
from django.contrib.auth.forms import AdminUserCreationForm

from anagrafiche.models import AnagraficaAzienda


class CreaUtenteAdminForm(AdminUserCreationForm):
    destinazione_azienda = forms.ChoiceField(
        label='Azienda', required=False, initial='attuale',
        choices=[
            ('attuale', 'Stessa azienda dell’amministratore'),
            ('nuova', 'Nuova azienda — configurazione al primo accesso'),
        ],
        help_text='Con “Nuova azienda” l’utente sarà amministratore del proprio spazio e ne inserirà i dati al primo accesso.',
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('destinazione_azienda') == 'nuova' and not cleaned.get('set_usable_password'):
            self.add_error(None, 'Imposta una password per consentire al nuovo utente di accedere e configurare la sua azienda.')
        return cleaned


class ConfiguraAziendaForm(forms.ModelForm):
    ragione_sociale = forms.CharField(
        label='Nome o ragione sociale dell’azienda', max_length=200,
        widget=forms.TextInput(attrs={'class': 'input', 'autocomplete': 'organization', 'autofocus': True}),
    )

    class Meta:
        model = AnagraficaAzienda
        fields = [
            'ragione_sociale', 'partita_iva', 'codice_fiscale', 'email', 'telefono',
            'indirizzo_legale', 'comune_legale', 'cap_legale', 'prov_legale',
        ]
        labels = {
            'partita_iva': 'Partita IVA', 'codice_fiscale': 'Codice fiscale',
            'email': 'Email aziendale', 'telefono': 'Telefono',
            'indirizzo_legale': 'Indirizzo', 'comune_legale': 'Comune',
            'cap_legale': 'CAP', 'prov_legale': 'Provincia',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == 'ragione_sociale':
                continue
            field.required = False
            field.widget.attrs['class'] = 'select' if isinstance(field.widget, forms.Select) else 'input'
        province = self.fields['prov_legale']
        province.choices = [('', 'Seleziona…')] + [(v, label) for v, label in province.choices if v]
