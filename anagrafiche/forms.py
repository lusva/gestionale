"""Form per le anagrafiche fiscali."""
from decimal import Decimal

from django import forms

from clienti.forms import normalizza_partita_iva, valida_codice_fiscale

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

    def clean_partita_iva(self):
        """Stessa normalizzazione dell'import CSV: senza spazi, maiuscola,
        prefisso ``IT`` per le P.IVA italiane a 11 cifre."""
        piva, errore = normalizza_partita_iva(self.cleaned_data.get('partita_iva'))
        if errore:
            raise forms.ValidationError(errore)
        return piva or None

    def clean_codice_fiscale(self):
        cf = (self.cleaned_data.get('codice_fiscale') or '').strip().upper().replace(' ', '')
        if not cf:
            return None
        if not valida_codice_fiscale(cf):
            raise forms.ValidationError(
                'Codice fiscale non valido: attesi 16 caratteri (persona fisica) o 11 cifre',
            )
        return cf


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

    # Campi che non possono cambiare se la posizione è già usata nei documenti:
    # altererebbero retroattivamente l'IVA di fatture già emesse.
    CAMPI_BLOCCATI_SE_USATA = ('aliquota', 'natura')

    def usata_in_documenti(self) -> bool:
        pos = self.instance
        if not pos.pk:
            return False
        for rel in PosizioneIva._meta.related_objects:
            if rel.related_model._meta.app_label != 'documenti':
                continue
            if rel.many_to_many:
                continue
            if rel.related_model._default_manager.filter(
                **{rel.field.name: pos},
            ).exists():
                return True
        return False

    def clean(self):
        cleaned = super().clean()
        if not self.instance.pk:
            return cleaned
        cambiati = []
        for campo in self.CAMPI_BLOCCATI_SE_USATA:
            if campo not in cleaned:
                continue
            nuovo, vecchio = cleaned[campo], getattr(self.instance, campo)
            if campo == 'aliquota' and nuovo is not None and vecchio is not None:
                if Decimal(nuovo) == Decimal(vecchio):
                    continue
            elif (nuovo or '') == (vecchio or ''):
                continue
            cambiati.append(campo)
        if cambiati and self.usata_in_documenti():
            for campo in cambiati:
                self.add_error(
                    campo,
                    'Posizione IVA già usata in righe di documenti: non puoi '
                    'modificarne aliquota o natura. Crea una nuova posizione IVA.',
                )
        return cleaned


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


class BaseScadenzaFormSet(forms.BaseInlineFormSet):
    """Le percentuali delle rate (escluse quelle eliminate) devono fare 100."""

    TOLLERANZA = Decimal('0.01')

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        totale = Decimal('0')
        rate = 0
        for form in self.forms:
            if not hasattr(form, 'cleaned_data') or not form.cleaned_data:
                continue
            if self.can_delete and self._should_delete_form(form):
                continue
            perc = form.cleaned_data.get('percentuale')
            if perc is None:
                continue
            totale += perc
            rate += 1
        if rate and abs(totale - Decimal('100')) > self.TOLLERANZA:
            raise forms.ValidationError(
                f'La somma delle percentuali delle scadenze deve essere 100% '
                f'(attuale: {totale}%).',
            )


ScadenzaFormSet = forms.inlineformset_factory(
    parent_model=FormePagamento,
    model=Scadenza,
    form=ScadenzaForm,
    formset=BaseScadenzaFormSet,
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
            'liquidazione_iva', 'modulo_magazzino_attivo',
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
            'liquidazione_iva': SELECT,
            'firma_motivo': TEXT,
            'logo': forms.FileInput(attrs={'accept': 'image/*', 'class': 'logo-input'}),
            'certificato_p12': forms.FileInput(attrs={'class': 'input', 'accept': '.p12,.pfx'}),
            'certificato_password': forms.PasswordInput(
                attrs={'class': 'input', 'autocomplete': 'new-password',
                       'placeholder': 'Lascia vuoto per non modificarla'},
                render_value=False,
            ),
        }

    def clean_certificato_password(self):
        """La password non viene mai re-renderizzata: un campo vuoto al
        salvataggio significa «mantieni quella esistente»."""
        pwd = self.cleaned_data.get('certificato_password') or ''
        if not pwd and self.instance and self.instance.pk:
            return self.instance.certificato_password
        return pwd
