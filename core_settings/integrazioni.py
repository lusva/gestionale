"""Impostazioni › Integrazioni: invio delle fatture al SDI tramite la PEC.

Configurazione della casella PEC (prima si faceva solo dall'admin di
Django), verifica della connessione senza invii, stato della ricezione
(`sdi_pull`) e ultimi messaggi del SDI. Solo per chi ha
``impostazioni.modifica``: qui ci sono le credenziali della PEC aziendale.
"""
from __future__ import annotations

import io
from dataclasses import asdict
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone

from accounts.permissions import require_perm
from anagrafiche.models import AnagraficaAzienda
from audit.models import Azione
from audit.utils import log as registra_audit

# Dati pubblici del gestore (vedi README, «Fatturazione elettronica»): per
# gli altri gestori host e porte si scrivono a mano, senza tirare a indovinare.
PRESET_ARUBA = {
    'pec_smtp_host': 'smtps.pec.aruba.it', 'pec_smtp_port': '465',
    'pec_imap_host': 'imaps.pec.aruba.it', 'pec_imap_port': '993',
}

CAMPI = [
    'sdi_provider', 'pec_mittente', 'pec_destinatario_sdi',
    'pec_smtp_host', 'pec_smtp_port', 'pec_smtp_user', 'pec_smtp_password', 'pec_smtp_use_tls',
    'pec_imap_host', 'pec_imap_port', 'pec_imap_user', 'pec_imap_password', 'pec_imap_folder',
]
PASSWORD = {'pec_smtp_password': 'cancella_smtp_password',
            'pec_imap_password': 'cancella_imap_password'}

# Oltre quest'intervallo senza un controllo della PEC il timer (ogni 15
# minuti) probabilmente non gira.
CONTROLLO_IN_RITARDO = timedelta(hours=1)

_SESSIONE_ESITI = 'integrazioni_esiti'


def _password_widget():
    return forms.PasswordInput(render_value=False, attrs={
        'class': 'input', 'autocomplete': 'new-password',
    })


class IntegrazioniForm(forms.ModelForm):
    cancella_smtp_password = forms.BooleanField(required=False, label='Cancella la password salvata')
    cancella_imap_password = forms.BooleanField(required=False, label='Cancella la password salvata')

    class Meta:
        model = AnagraficaAzienda
        fields = CAMPI
        labels = {
            'sdi_provider': 'Invio al SDI',
            'pec_mittente': 'Indirizzo PEC dell\'azienda',
            'pec_destinatario_sdi': 'Casella PEC del SDI',
            'pec_smtp_host': 'Server', 'pec_smtp_port': 'Porta',
            'pec_smtp_user': 'Utente', 'pec_smtp_password': 'Password',
            'pec_smtp_use_tls': 'STARTTLS (solo su porte diverse da 465)',
            'pec_imap_host': 'Server', 'pec_imap_port': 'Porta',
            'pec_imap_user': 'Utente', 'pec_imap_password': 'Password',
            'pec_imap_folder': 'Cartella',
        }
        widgets = {
            'sdi_provider': forms.Select(attrs={'class': 'select'}),
            'pec_mittente': forms.EmailInput(attrs={'class': 'input', 'placeholder': 'fatture@pec.azienda.it'}),
            'pec_destinatario_sdi': forms.EmailInput(attrs={'class': 'input mono'}),
            'pec_smtp_host': forms.TextInput(attrs={'class': 'input mono'}),
            'pec_smtp_port': forms.NumberInput(attrs={'class': 'input mono', 'placeholder': '465'}),
            'pec_smtp_user': forms.TextInput(attrs={'class': 'input', 'autocomplete': 'off'}),
            'pec_smtp_password': _password_widget(),
            'pec_imap_host': forms.TextInput(attrs={'class': 'input mono'}),
            'pec_imap_port': forms.NumberInput(attrs={'class': 'input mono', 'placeholder': '993'}),
            'pec_imap_user': forms.TextInput(attrs={'class': 'input', 'autocomplete': 'off'}),
            'pec_imap_password': _password_widget(),
            'pec_imap_folder': forms.TextInput(attrs={'class': 'input mono'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Il backend «mock» segna le fatture come inviate senza inviarle:
        # in produzione non deve essere una scelta possibile per sbaglio.
        if not settings.DEBUG and self.instance.sdi_provider != AnagraficaAzienda.SdiProvider.MOCK:
            self.fields['sdi_provider'].choices = [
                c for c in self.fields['sdi_provider'].choices
                if c[0] != AnagraficaAzienda.SdiProvider.MOCK
            ]
        for campo in PASSWORD:
            self.fields[campo].required = False
            if getattr(self.instance, campo):
                self.fields[campo].widget.attrs['placeholder'] = 'Salvata · lascia vuoto per tenerla'

    def clean(self):
        dati = super().clean()
        # La password non torna mai nell'HTML: vuota = tieni quella salvata.
        for campo, cancella in PASSWORD.items():
            if dati.get(cancella):
                dati[campo] = ''
            elif not dati.get(campo):
                dati[campo] = getattr(self.instance, campo)
        if dati.get('sdi_provider') == AnagraficaAzienda.SdiProvider.PEC:
            obbligatori = {
                'pec_mittente': 'Serve l\'indirizzo PEC da cui partono le fatture.',
                'pec_smtp_host': 'Serve il server di invio.',
                'pec_smtp_user': 'Serve l\'utente della casella.',
                'pec_smtp_password': 'Serve la password della casella.',
            }
            for campo, errore in obbligatori.items():
                if not dati.get(campo) and campo not in self.errors:
                    self.add_error(campo, errore)
        return dati


def _campi_modificati(prima: dict, az) -> list[str]:
    """Nomi dei campi cambiati, per l'audit: delle password solo il fatto."""
    modificati = []
    for campo in CAMPI:
        if prima[campo] != getattr(az, campo):
            modificati.append(campo)
    return modificati


@require_perm('impostazioni.modifica')
def integrazioni(request):
    from core_settings.views import _base_context

    az = AnagraficaAzienda.current()
    if request.method == 'POST':
        azione = request.POST.get('azione', 'salva')
        if azione == 'controlla':
            return _controlla_pec(request)
        if azione == 'verifica':
            return _verifica(request, az)

        prima = {c: getattr(az, c) for c in CAMPI}
        form = IntegrazioniForm(request.POST, instance=az)
        if form.is_valid():
            az = form.save()
            modificati = _campi_modificati(prima, az)
            if modificati:
                registra_audit(Azione.UPDATE, request=request, target=az,
                               meta={'sezione': 'integrazioni', 'campi': modificati})
                messages.success(request, 'Impostazioni della PEC salvate.')
            else:
                messages.info(request, 'Nessuna modifica.')
            if azione == 'salva_verifica':
                return _verifica(request, az)
            return redirect('settings:integrazioni')
    else:
        form = IntegrazioniForm(instance=az)

    ctx = _base_context(request, 'integrazioni')
    ctx.update(_stato(az))
    ctx.update({
        'form': form,
        'azienda': az,
        'preset_aruba': PRESET_ARUBA,
        'esiti': request.session.pop(_SESSIONE_ESITI, None),
        'controllo': request.session.pop('integrazioni_controllo', None),
    })
    return render(request, 'settings/integrazioni.html', ctx)


def _verifica(request, az):
    """Login SMTP e IMAP con i dati salvati, nessun invio. L'esito si mostra
    dopo il redirect (un aggiornamento della pagina non riprova)."""
    from documenti.sdi import verifica_configurazione

    esiti = verifica_configurazione(az)
    request.session[_SESSIONE_ESITI] = [asdict(e) for e in esiti]
    return redirect('settings:integrazioni')


def _controlla_pec(request):
    """``sdi_pull`` subito, su poche email (resta ben dentro il timeout della
    richiesta): le altre le prende il timer."""
    from django.core.management import CommandError, call_command

    uscita = io.StringIO()
    try:
        call_command('sdi_pull', limit=30, stdout=uscita, stderr=uscita)
        ok = True
    except CommandError as exc:
        uscita.write(f'\n{exc}')
        ok = False
    request.session['integrazioni_controllo'] = {
        'ok': ok, 'testo': uscita.getvalue().strip() or 'Nessun nuovo messaggio.',
    }
    return redirect('settings:integrazioni')


def _stato(az) -> dict:
    """Riquadro di stato: ricezione, ultimi messaggi del SDI, fatture da
    correggere. Poche query, nessuna per riga."""
    from core_settings.esecuzioni import ultima
    from documenti.models import MessaggioPecElaborato, MessaggioSdi, TestataFattura, TestataNotaCredito

    pec_attiva = az.sdi_provider == AnagraficaAzienda.SdiProvider.PEC
    controllo = ultima('sdi_pull')
    ultimo_alle = controllo.ultima_fine if controllo else None
    return {
        'pec_attiva': pec_attiva,
        'casella_sdi_primo_invio': (az.pec_destinatario_sdi or '').lower() in ('', 'sdi01@pec.fatturapa.it'),
        'ultimo_controllo': controllo,
        'controllo_in_ritardo': pec_attiva and (
            ultimo_alle is None or timezone.now() - ultimo_alle > CONTROLLO_IN_RITARDO
        ),
        'ultima_email': MessaggioPecElaborato.objects.order_by('-elaborato_il').first(),
        'messaggi_sdi': (MessaggioSdi.objects.select_related('fattura', 'nota_credito')
                         .order_by('-timestamp')[:10]),
        'fatture_da_correggere': (
            list(TestataFattura.objects.filter(sdi_stato__in=TestataFattura.SDI_STATI_REINVIO)
                 .select_related('cliente').order_by('-data_documento')[:20])
            + list(TestataNotaCredito.objects.filter(sdi_stato__in=TestataNotaCredito.SDI_STATI_REINVIO)
                   .select_related('cliente').order_by('-data_documento')[:20])
        ),
    }
