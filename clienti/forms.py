import re

from django import forms

from .models import Cliente, Contatto, Settore, TipoCliente

try:
    from stdnum.it import iva as stdnum_iva
except ImportError:
    stdnum_iva = None


PIVA_RE = re.compile(r'^(IT)?\d{11}$')
# P.IVA estera: prefisso paese ISO (2 lettere) + 2-14 caratteri alfanumerici.
PIVA_ESTERA_RE = re.compile(r'^[A-Z]{2}[A-Z0-9]{2,14}$')
# Codice fiscale persona fisica (con omocodia) o numerico a 11 cifre (soggetti
# diversi dalle persone fisiche).
CF_PERSONA_RE = re.compile(
    r'^[A-Z]{6}[0-9LMNPQRSTUV]{2}[ABCDEHLMPRST][0-9LMNPQRSTUV]{2}'
    r'[A-Z][0-9LMNPQRSTUV]{3}[A-Z]$'
)
CF_NUMERICO_RE = re.compile(r'^\d{11}$')
SDI_RE = re.compile(r'^[A-Z0-9]{7}$')


def normalizza_partita_iva(raw, nazione='IT'):
    """Normalizza una P.IVA. Ritorna ``(valore, errore)``.

    - Italia: 11 cifre, salvata con prefisso ``IT``.
    - Estero: prefisso paese (se manca si usa ``nazione``) + alfanumerici.
    Una stringa vuota ritorna ``('', None)``.
    """
    piva = (raw or '').strip().upper().replace(' ', '').replace('.', '').replace('-', '')
    nazione = (nazione or 'IT').strip().upper() or 'IT'
    if not piva:
        return '', None
    if nazione == 'IT' or piva.startswith('IT'):
        piva_norm = piva[2:] if piva.startswith('IT') else piva
        if not PIVA_RE.match(f'IT{piva_norm}'):
            return None, 'Formato non valido: attesi 11 caratteri numerici (es. IT12345678901)'
        return f'IT{piva_norm}', None
    if not piva[:2].isalpha():
        piva = f'{nazione}{piva}'
    if not PIVA_ESTERA_RE.match(piva) or len(piva) > 16:
        return None, ('Formato non valido: attesi prefisso paese di 2 lettere '
                      'e caratteri alfanumerici (es. DE123456789)')
    return piva, None


def valida_codice_fiscale(cf):
    """True se ``cf`` è un codice fiscale italiano formalmente valido."""
    return bool(CF_PERSONA_RE.match(cf) or CF_NUMERICO_RE.match(cf))


class ClienteForm(forms.ModelForm):
    ref_nome = forms.CharField(
        label='Nome referente', required=False,
        widget=forms.TextInput(attrs={'class': 'input'}),
    )
    ref_cognome = forms.CharField(
        label='Cognome referente', required=False,
        widget=forms.TextInput(attrs={'class': 'input'}),
    )
    ref_email = forms.EmailField(
        label='Email referente', required=False,
        widget=forms.EmailInput(attrs={'class': 'input', 'placeholder': 'nome@dominio.it'}),
    )
    ref_tel = forms.CharField(
        label='Telefono referente', required=False,
        widget=forms.TextInput(attrs={'class': 'input', 'placeholder': '+39 …'}),
    )

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
        # La validazione dipende da tipo/nazione: vedi clean().
        return (self.cleaned_data.get('partita_iva') or '').strip().upper().replace(' ', '')

    def clean_codice_fiscale(self):
        return (self.cleaned_data.get('codice_fiscale') or '').strip().upper().replace(' ', '')

    def clean_codice_sdi(self):
        sdi = (self.cleaned_data.get('codice_sdi') or '').strip().upper()
        if sdi and not SDI_RE.match(sdi):
            raise forms.ValidationError('Il codice SDI deve essere di 7 caratteri alfanumerici')
        return sdi

    def clean_nazione(self):
        return (self.cleaned_data.get('nazione') or 'IT').strip().upper() or 'IT'

    def clean(self):
        cleaned = super().clean()
        tipo = cleaned.get('tipo') or TipoCliente.AZIENDA
        nazione = cleaned.get('nazione') or 'IT'
        italiano = nazione == 'IT'

        if 'partita_iva' in cleaned:
            piva, errore = normalizza_partita_iva(cleaned['partita_iva'], nazione)
            if errore:
                self.add_error('partita_iva', errore)
            else:
                cleaned['partita_iva'] = piva
                if not piva and tipo == TipoCliente.AZIENDA and italiano:
                    self.add_error('partita_iva', 'Partita IVA obbligatoria per le aziende italiane')
            # Il checksum stdnum è disponibile come endpoint separato
            # (/valida-piva/) ma non è bloccante al salvataggio per permettere
            # la modifica di anagrafiche storiche.

        cf = cleaned.get('codice_fiscale', '')
        if cf and italiano:
            if tipo == TipoCliente.PRIVATO and not CF_PERSONA_RE.match(cf):
                self.add_error(
                    'codice_fiscale',
                    'Codice fiscale non valido: attesi 16 caratteri (es. RSSMRA80A01H501U)',
                )
            elif not valida_codice_fiscale(cf):
                self.add_error(
                    'codice_fiscale',
                    'Codice fiscale non valido: attesi 16 caratteri (persona fisica) o 11 cifre',
                )
        if tipo == TipoCliente.PRIVATO and italiano and not cf \
                and 'codice_fiscale' not in self.errors:
            self.add_error('codice_fiscale', 'Codice fiscale obbligatorio per i privati')
        return cleaned

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
