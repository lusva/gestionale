"""Form per le anagrafiche fiscali."""
from django import forms

from .models import (
    AnagraficaAzienda,
    Articolo,
    CategoriaCosto,
    FormePagamento,
    Fornitore,
    PosizioneIva,
    Scadenza,
)


SELECT = forms.Select(attrs={'class': 'select'})
TEXT = forms.TextInput(attrs={'class': 'input'})
TEXT_MONO = forms.TextInput(attrs={'class': 'input mono'})
DEC = forms.NumberInput(attrs={'class': 'input mono', 'step': '0.01'})
INT = forms.NumberInput(attrs={'class': 'input mono', 'step': '1'})
EMAIL = forms.EmailInput(attrs={'class': 'input'})
TEL = forms.TextInput(attrs={'class': 'input'})
TEXTAREA = forms.Textarea(attrs={'class': 'textarea', 'rows': 3})
CHECK = forms.CheckboxInput()


class FornitoreForm(forms.ModelForm):
    class Meta:
        model = Fornitore
        fields = [
            'ragione_sociale', 'nome', 'cognome',
            'partita_iva', 'codice_fiscale',
            'email', 'telefono',
            'fatturazione_elettronica',
        ]
        widgets = {
            'ragione_sociale': TEXT, 'nome': TEXT, 'cognome': TEXT,
            'partita_iva': TEXT_MONO, 'codice_fiscale': TEXT_MONO,
            'email': EMAIL, 'telefono': TEL,
            'fatturazione_elettronica': SELECT,
        }


class ArticoloForm(forms.ModelForm):
    class Meta:
        model = Articolo
        fields = [
            'codice', 'descrizione', 'scelta',
            'um', 'prezzo_listino', 'posizione_iva', 'obsoleto',
        ]
        widgets = {
            'codice': TEXT_MONO, 'descrizione': TEXT, 'scelta': TEXT,
            'um': SELECT, 'prezzo_listino': DEC,
            'posizione_iva': SELECT,
        }


class PosizioneIvaForm(forms.ModelForm):
    class Meta:
        model = PosizioneIva
        fields = [
            'descrizione', 'aliquota', 'esigibilita_iva',
            'natura', 'reverse_charge', 'scissione_pagamenti',
            'bollo', 'esente',
        ]
        widgets = {
            'descrizione': TEXT,
            'aliquota': DEC,
            'esigibilita_iva': SELECT,
            'natura': SELECT,
        }


class CategoriaCostoForm(forms.ModelForm):
    class Meta:
        model = CategoriaCosto
        fields = ['codice', 'descrizione', 'ordinamento', 'attiva']
        widgets = {
            'codice': TEXT_MONO, 'descrizione': TEXT,
            'ordinamento': INT,
        }


class FormePagamentoForm(forms.ModelForm):
    class Meta:
        model = FormePagamento
        fields = ['tipo_pagamento', 'modalita_pagamento', 'conto_corrente_cliente']
        widgets = {
            'tipo_pagamento': TEXT,
            'modalita_pagamento': SELECT,
        }


class ScadenzaForm(forms.ModelForm):
    class Meta:
        model = Scadenza
        fields = ['numero_giorni', 'percentuale', 'fine_mese', 'numero_giorni_fm']
        widgets = {
            'numero_giorni': INT, 'percentuale': DEC,
            'numero_giorni_fm': INT,
        }


ScadenzaFormSet = forms.inlineformset_factory(
    parent_model=FormePagamento,
    model=Scadenza,
    form=ScadenzaForm,
    fk_name='forme_pagamento',
    extra=1,
    can_delete=True,
)


class AnagraficaAziendaForm(forms.ModelForm):
    """Form singleton per l'anagrafica dell'azienda titolare del gestionale."""

    class Meta:
        model = AnagraficaAzienda
        fields = [
            'ragione_sociale', 'partita_iva', 'codice_fiscale',
            'indirizzo_legale', 'comune_legale', 'cap_legale', 'prov_legale',
            'indirizzo_op', 'comune_op', 'cap_op',
            'email', 'telefono',
            'logo', 'intestazione_documenti', 'contenuto_footer',
            'fatturazione_elettronica', 'profilo_fiscale',
            'modulo_magazzino_attivo',
            'certificato_p12', 'certificato_password', 'firma_motivo',
        ]
        widgets = {
            'ragione_sociale': TEXT, 'partita_iva': TEXT_MONO,
            'codice_fiscale': TEXT_MONO,
            'indirizzo_legale': TEXT, 'comune_legale': TEXT,
            'cap_legale': TEXT_MONO, 'prov_legale': SELECT,
            'indirizzo_op': TEXT, 'comune_op': TEXT,
            'cap_op': TEXT_MONO,
            'email': EMAIL, 'telefono': TEL,
            'intestazione_documenti': TEXT,
            'contenuto_footer': TEXTAREA,
            'fatturazione_elettronica': SELECT,
            'profilo_fiscale': SELECT,
            'firma_motivo': TEXT,
            'logo': forms.FileInput(attrs={'accept': 'image/*', 'class': 'logo-input'}),
            'certificato_p12': forms.FileInput(attrs={'class': 'input', 'accept': '.p12,.pfx'}),
            'certificato_password': forms.PasswordInput(
                attrs={'class': 'input', 'autocomplete': 'new-password'},
                render_value=True,
            ),
        }
