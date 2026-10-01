"""Form per il modulo Cashflow."""
from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from accounts.models import Ruolo

from .models import (
    RimborsoChilometrico, ScadenzaFiscale, ScadenzaSpesa, SpesaRicorrente,
    StatoRimborso, puo_approvare_rimborsi,
)


SELECT = forms.Select(attrs={'class': 'select'})
TEXT = forms.TextInput(attrs={'class': 'input'})
DEC = forms.NumberInput(attrs={'class': 'input mono', 'step': '0.01'})
INT = forms.NumberInput(attrs={'class': 'input mono', 'step': '1', 'min': '1', 'max': '31'})
DATE = forms.DateInput(attrs={'class': 'input', 'type': 'date'})
TEXTAREA = forms.Textarea(attrs={'class': 'textarea', 'rows': 3})
CHECKBOX = forms.CheckboxInput(attrs={'class': 'checkbox', 'data-toggle-riepilogo-mensile': '1'})


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

    def clean_giorno_del_mese(self):
        giorno = self.cleaned_data.get('giorno_del_mese')
        if giorno is not None and not 1 <= giorno <= 31:
            raise forms.ValidationError('Il giorno del mese deve essere tra 1 e 31.')
        return giorno

    def clean(self):
        cleaned = super().clean()
        inizio, fine = cleaned.get('data_inizio'), cleaned.get('data_fine')
        if inizio and fine and fine < inizio:
            self.add_error('data_fine', 'La data di fine precede la data di inizio.')
        return cleaned


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


class RimborsoChilometricoForm(forms.ModelForm):
    class Meta:
        model = RimborsoChilometrico
        fields = [
            'data', 'amministratore', 'is_riepilogo_mensile',
            'partenza', 'destinazione', 'km', 'tariffa_km', 'importo',
            'cliente', 'causale',
            'stato', 'data_pagamento', 'allegato', 'note',
        ]
        widgets = {
            'data': DATE,
            'amministratore': SELECT,
            'is_riepilogo_mensile': CHECKBOX,
            'partenza': forms.TextInput(
                attrs={'class': 'input', 'placeholder': 'Es. Modena (sede)'},
            ),
            'destinazione': forms.TextInput(
                attrs={'class': 'input', 'placeholder': 'Es. Milano — Cliente X'},
            ),
            'km': forms.NumberInput(
                attrs={'class': 'input mono', 'step': '0.01', 'min': '0'},
            ),
            'tariffa_km': forms.NumberInput(
                attrs={'class': 'input mono', 'step': '0.0001', 'min': '0'},
            ),
            'importo': forms.NumberInput(
                attrs={'class': 'input mono', 'step': '0.01', 'min': '0'},
            ),
            'cliente': SELECT,
            'causale': forms.TextInput(
                attrs={'class': 'input', 'placeholder': 'Es. Sopralluogo cantiere'},
            ),
            'stato': SELECT,
            'data_pagamento': DATE,
            'note': TEXTAREA,
        }

    # Campi modificabili solo da chi può approvare i rimborsi.
    CAMPI_APPROVAZIONE = ('stato', 'data_pagamento', 'amministratore')

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        User = get_user_model()
        filtro = Q(profile__ruolo=Ruolo.AMMINISTRATORE)
        utente_corrente = getattr(request, 'user', None)
        self._utente = utente_corrente
        self._stato_iniziale = self.instance.stato if self.instance.pk else None
        self._amministratore_iniziale = self.instance.amministratore_id
        self.puo_approvare = puo_approvare_rimborsi(utente_corrente)
        if utente_corrente is not None and utente_corrente.is_authenticated:
            filtro = filtro | Q(pk=utente_corrente.pk)
        if self.instance and self.instance.amministratore_id:
            filtro = filtro | Q(pk=self.instance.amministratore_id)
        self.fields['amministratore'].queryset = (
            User.objects.filter(filtro).distinct()
            .order_by('first_name', 'last_name', 'username')
        )
        self.fields['amministratore'].label_from_instance = (
            lambda u: u.get_full_name() or u.username
        )
        self.fields['importo'].required = False
        self.fields['importo'].help_text = (
            'Compilato solo per i riepiloghi mensili. Per le trasferte singole '
            'è calcolato automaticamente come km × tariffa.'
        )
        if not self.puo_approvare:
            # I campi disabilitati ignorano i dati POST e usano l'initial.
            if not self.instance.pk and utente_corrente is not None \
                    and utente_corrente.is_authenticated:
                self.initial['amministratore'] = utente_corrente.pk
            for campo in self.CAMPI_APPROVAZIONE:
                self.fields[campo].disabled = True
                self.fields[campo].help_text = (
                    'Modificabile solo da un amministratore.'
                )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('is_riepilogo_mensile'):
            importo = cleaned.get('importo')
            if importo in (None, ''):
                self.add_error(
                    'importo',
                    'Inserisci l\'importo totale del riepilogo mensile.',
                )
        else:
            mancanti = [
                campo for campo in ('partenza', 'destinazione', 'km', 'tariffa_km')
                if cleaned.get(campo) in (None, '')
            ]
            for campo in mancanti:
                self.add_error(campo, 'Campo obbligatorio per le trasferte singole.')
        # Niente auto-approvazione: chi è l'amministratore del rimborso non può
        # portarlo (o mantenerlo, cambiandone il titolare) in approvato/pagato.
        stato = cleaned.get('stato')
        amministratore = cleaned.get('amministratore')
        utente = self._utente
        if (
            utente is not None and utente.is_authenticated
            and amministratore is not None and amministratore.pk == utente.pk
            and stato in (StatoRimborso.APPROVATO, StatoRimborso.PAGATO)
            and (
                stato != self._stato_iniziale
                or amministratore.pk != self._amministratore_iniziale
            )
        ):
            self.add_error(
                'stato',
                'Non puoi approvare o pagare un tuo rimborso: '
                'serve un altro amministratore.',
            )
        return cleaned
