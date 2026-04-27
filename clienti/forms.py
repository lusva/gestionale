import re

from django import forms

from .models import Cliente, Contatto, Settore

try:
    from stdnum.it import iva as stdnum_iva
except ImportError:
    stdnum_iva = None


PIVA_RE = re.compile(r'^(IT)?\d{11}$')


class ClienteForm(forms.ModelForm):
    ref_nome = forms.CharField(label='Nome referente', required=False)
    ref_cognome = forms.CharField(label='Cognome referente', required=False)
    ref_email = forms.EmailField(label='Email referente', required=False)
    ref_tel = forms.CharField(label='Telefono referente', required=False)

    settore = forms.ModelChoiceField(
        queryset=Settore.objects.all(),
        required=False,
        empty_label='— Seleziona settore —',
        widget=forms.Select(attrs={'class': 'select'}),
    )

    class Meta:
        model = Cliente
        fields = [
            'ragione_sociale', 'tipo', 'settore',
            'partita_iva', 'codice_fiscale', 'codice_sdi', 'pec',
            'indirizzo', 'cap', 'citta', 'provincia', 'nazione',
            'stato', 'account_manager', 'tags', 'note',
        ]
        widgets = {
            'ragione_sociale': forms.TextInput(attrs={'class': 'input', 'placeholder': 'Es. Rossi & Figli S.r.l.'}),
            'tipo': forms.Select(attrs={'class': 'select'}),
            'settore': forms.Select(attrs={'class': 'select'}),
            'partita_iva': forms.TextInput(attrs={'class': 'input mono', 'placeholder': 'IT00000000000'}),
            'codice_fiscale': forms.TextInput(attrs={'class': 'input mono', 'placeholder': 'RSSMRA80A01H501U'}),
            'codice_sdi': forms.TextInput(attrs={'class': 'input mono', 'placeholder': '0000000', 'maxlength': 7}),
            'pec': forms.EmailInput(attrs={'class': 'input', 'placeholder': 'azienda@pec.it'}),
            'indirizzo': forms.TextInput(attrs={'class': 'input', 'placeholder': 'Via, numero civico'}),
            'cap': forms.TextInput(attrs={'class': 'input mono', 'placeholder': '00000', 'maxlength': 5}),
            'citta': forms.TextInput(attrs={'class': 'input', 'placeholder': 'Milano'}),
            'provincia': forms.TextInput(attrs={'class': 'input', 'placeholder': 'MI', 'maxlength': 2}),
            'nazione': forms.TextInput(attrs={'class': 'input mono', 'maxlength': 2}),
            'stato': forms.Select(attrs={'class': 'select'}),
            'account_manager': forms.Select(attrs={'class': 'select'}),
            'tags': forms.SelectMultiple(attrs={'class': 'select', 'size': 5}),
            'note': forms.Textarea(attrs={'class': 'textarea', 'rows': 3, 'placeholder': 'Informazioni aggiuntive…'}),
        }

    def clean_partita_iva(self):
        piva = (self.cleaned_data.get('partita_iva') or '').strip().upper().replace(' ', '')
        if not piva:
            raise forms.ValidationError('Partita IVA obbligatoria')
        piva_norm = piva[2:] if piva.startswith('IT') else piva
        if not PIVA_RE.match(f'IT{piva_norm}'):
            raise forms.ValidationError('Formato non valido: attesi 11 caratteri numerici (es. IT12345678901)')
        # Il checksum stdnum è disponibile come endpoint separato (/valida-piva/)
        # ma non è bloccante al salvataggio per permettere la modifica di anagrafiche
        # storiche con P.IVA non allineate al nuovo algoritmo.
        return f'IT{piva_norm}'

    def clean_cap(self):
        cap = (self.cleaned_data.get('cap') or '').strip()
        if cap and (not cap.isdigit() or len(cap) != 5):
            raise forms.ValidationError('Il CAP deve essere di 5 cifre')
        return cap

    def clean_provincia(self):
        pr = (self.cleaned_data.get('provincia') or '').strip().upper()
        return pr

    def save(self, commit=True):
        cliente = super().save(commit=commit)
        if commit:
            nome = self.cleaned_data.get('ref_nome', '').strip()
            cognome = self.cleaned_data.get('ref_cognome', '').strip()
            if nome or cognome:
                email = self.cleaned_data.get('ref_email', '')
                tel = self.cleaned_data.get('ref_tel', '')
                Contatto.objects.update_or_create(
                    cliente=cliente, primary=True,
                    defaults={
                        'nome': nome, 'cognome': cognome,
                        'email': email, 'telefono': tel,
                    },
                )
        return cliente


class ContattoForm(forms.ModelForm):
    class Meta:
        model = Contatto
        fields = ['nome', 'cognome', 'ruolo', 'email', 'telefono', 'primary', 'note']
        widgets = {
            'nome': forms.TextInput(attrs={'class': 'input'}),
            'cognome': forms.TextInput(attrs={'class': 'input'}),
            'ruolo': forms.TextInput(attrs={'class': 'input', 'placeholder': 'CEO, CTO, …'}),
            'email': forms.EmailInput(attrs={'class': 'input'}),
            'telefono': forms.TextInput(attrs={'class': 'input', 'placeholder': '+39 …'}),
            'note': forms.Textarea(attrs={'class': 'textarea', 'rows': 2}),
        }
