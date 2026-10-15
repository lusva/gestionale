"""Fatture di acquisto inserite a mano: fornitori esteri, fatture cartacee,
tutto quello che non arriva come XML dal SDI.

Le fatture importate dall'XML si aprono nello stesso form in versione
ridotta: stato, data di registrazione, note, allegato e categorie di costo
delle righe; importi e righe fanno fede all'XML.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.permissions import require_perm

from .anteprima import _righe_valide
from .forms import (
    CategorieRigheFormSet,
    RigaFatturaAcquistoFormSet,
    ScadenzeAcquistoFormSet,
    TestataFatturaAcquistoForm,
    TestataFatturaAcquistoXmlForm,
)
from .models import TestataFatturaAcquisto, totali_acquisto
from .servizi import (
    DatiNonValidi,
    base_scadenze_acquisto,
    elimina_fattura_acquisto,
    ha_pagamenti_acquisto,
    motivo_non_eliminabile_acquisto,
    scadenze_acquisto_bloccate,
    sincronizza_scadenze_acquisto,
)
from .utils import genera_scadenze_da_forma_pagamento


def _render(request, fattura, form, righe_fs=None, scadenze_fs=None, categorie_fs=None):
    ctx = {
        'active_nav': 'fatture_acquisto',
        'fattura': fattura,
        'is_create': not fattura.pk,
        'form': form,
        'righe_formset': righe_fs,
        'scadenze_formset': scadenze_fs,
        'categorie_formset': categorie_fs,
        'scadenze_bloccate': scadenze_acquisto_bloccate(fattura) if fattura.pk else '',
    }
    if fattura.da_xml:
        return render(request, 'documenti/fattura_acquisto_form_xml.html', ctx)
    return render(request, 'documenti/fattura_acquisto_form.html', ctx)


@require_perm('documenti.modifica')
def fattura_acquisto_nuova(request):
    fattura = TestataFatturaAcquisto()
    if request.method != 'POST':
        iniziali = {}
        fornitore = request.GET.get('fornitore', '')
        if fornitore.isdigit():
            from anagrafiche.models import Fornitore
            f = Fornitore.objects.filter(pk=fornitore).first()
            if f is not None:
                iniziali = {'fornitore': f.pk, 'forme_pagamento': f.forme_pagamento_id}
        return _render(request, fattura, TestataFatturaAcquistoForm(initial=iniziali),
                       RigaFatturaAcquistoFormSet(instance=fattura, prefix='righe'),
                       ScadenzeAcquistoFormSet(instance=fattura, prefix='scadenze'))

    form = TestataFatturaAcquistoForm(request.POST, request.FILES, instance=fattura)
    righe_fs = RigaFatturaAcquistoFormSet(request.POST, instance=fattura, prefix='righe')
    scadenze_fs = ScadenzeAcquistoFormSet(request.POST, instance=fattura, prefix='scadenze')
    if not (form.is_valid() and righe_fs.is_valid() and scadenze_fs.is_valid()):
        return _render(request, fattura, form, righe_fs, scadenze_fs)
    with transaction.atomic():
        fattura = form.save()
        righe_fs.save()          # i signal delle righe ricalcolano i totali
        scadenze_fs.save()
        if not scadenze_fs.has_changed():
            sincronizza_scadenze_acquisto(fattura)
    messages.success(request, f'Fattura di acquisto {fattura.numero}/{fattura.anno} registrata.')
    return redirect('documenti:fattura_acquisto_detail', pk=fattura.pk)


@require_perm('documenti.modifica')
def fattura_acquisto_modifica(request, pk):
    fattura = get_object_or_404(TestataFatturaAcquisto, pk=pk)
    if fattura.da_xml:
        return _modifica_xml(request, fattura)

    # con pagamenti registrati le scadenze restano come sono: cancellarle o
    # cambiarle dal form cancellerebbe i pagamenti
    con_pagamenti = ha_pagamenti_acquisto(fattura)
    if request.method != 'POST':
        return _render(request, fattura, TestataFatturaAcquistoForm(instance=fattura),
                       RigaFatturaAcquistoFormSet(instance=fattura, prefix='righe'),
                       None if con_pagamenti else ScadenzeAcquistoFormSet(instance=fattura, prefix='scadenze'))

    prima = base_scadenze_acquisto(fattura)     # prima che il form scriva sull'istanza
    form = TestataFatturaAcquistoForm(request.POST, request.FILES, instance=fattura)
    righe_fs = RigaFatturaAcquistoFormSet(request.POST, instance=fattura, prefix='righe')
    scadenze_fs = None if con_pagamenti else ScadenzeAcquistoFormSet(
        request.POST, instance=fattura, prefix='scadenze')
    validi = form.is_valid() and righe_fs.is_valid() and (scadenze_fs is None or scadenze_fs.is_valid())
    if not validi:
        return _render(request, fattura, form, righe_fs, scadenze_fs)
    with transaction.atomic():
        form.save()
        righe_fs.save()          # i signal delle righe ricalcolano i totali
        if scadenze_fs is not None:
            scadenze_fs.save()
            if not scadenze_fs.has_changed():
                sincronizza_scadenze_acquisto(fattura, prima=prima)
    messages.success(request, 'Fattura di acquisto aggiornata.')
    return redirect('documenti:fattura_acquisto_detail', pk=fattura.pk)


def _modifica_xml(request, fattura):
    if request.method != 'POST':
        return _render(request, fattura, TestataFatturaAcquistoXmlForm(instance=fattura),
                       categorie_fs=CategorieRigheFormSet(instance=fattura, prefix='righe'))
    form = TestataFatturaAcquistoXmlForm(request.POST, request.FILES, instance=fattura)
    categorie_fs = CategorieRigheFormSet(request.POST, instance=fattura, prefix='righe')
    if not (form.is_valid() and categorie_fs.is_valid()):
        return _render(request, fattura, form, categorie_fs=categorie_fs)
    with transaction.atomic():
        form.save()
        categorie_fs.save()
    messages.success(request, 'Fattura di acquisto aggiornata.')
    return redirect('documenti:fattura_acquisto_detail', pk=fattura.pk)


@require_perm('documenti.elimina')
def fattura_acquisto_elimina(request, pk):
    fattura = get_object_or_404(TestataFatturaAcquisto.objects.select_related('fornitore'), pk=pk)
    dettaglio = reverse('documenti:fattura_acquisto_detail', args=[pk])
    if request.method == 'POST':
        try:
            elimina_fattura_acquisto(fattura)
        except DatiNonValidi as e:
            messages.error(request, ' '.join(e.errori.values()))
            return redirect(dettaglio)
        messages.success(request, f'Fattura di acquisto {fattura.numero}/{fattura.anno} eliminata.')
        return redirect('documenti:fattura_acquisto_list')
    motivo = motivo_non_eliminabile_acquisto(fattura)
    if motivo:
        messages.error(request, motivo)
        return redirect(dettaglio)
    return render(request, 'documenti/confirm_delete.html', {
        'active_nav': 'fatture_acquisto',
        'object': fattura,
        'object_label': f'la fattura di acquisto {fattura.numero}/{fattura.anno} — '
                        f'{fattura.fornitore or ""} n. {fattura.numero_fornitore or "—"}',
        'back_url': dettaglio,
    })


@require_perm('documenti.modifica')
def fattura_acquisto_anteprima(request):
    """Riepilogo live del form (HTMX): totali e scadenze come risulterebbero
    salvando, con gli stessi conti del salvataggio. Non scrive niente."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    pk = request.POST.get('documento_pk', '')
    istanza = get_object_or_404(TestataFatturaAcquisto, pk=pk) if pk.isdigit() else TestataFatturaAcquisto()
    prima = base_scadenze_acquisto(istanza) if istanza.pk else None
    bloccate = scadenze_acquisto_bloccate(istanza) if istanza.pk else ''
    form = TestataFatturaAcquistoForm(request.POST, instance=istanza)
    form.is_valid()
    testata = form.instance
    testata.data_documento = testata.data_fornitore
    righe = _righe_valide(RigaFatturaAcquistoFormSet(request.POST, instance=istanza, prefix='righe'))
    valori = totali_acquisto(righe)
    ctx = {'tipo': 'fattura_acquisto', 'valori': valori, 'n_righe': len(righe)}
    ctx.update(_scadenze_previste(request.POST, istanza, testata, valori, prima, bloccate))
    return render(request, 'documenti/_riepilogo_documento.html', ctx)


def _scadenze_previste(post, istanza, testata, valori, prima, bloccate) -> dict:
    """Stessa regola di ``sincronizza_scadenze_acquisto``: quelle del form se
    toccate, quelle attuali se bloccate o se forma, data e totale non
    cambiano, altrimenti quelle della forma di pagamento."""
    attuali = ([{'data': s.data_scadenza, 'importo': s.importo} for s in istanza.scadenze.all()]
               if istanza.pk else [])
    if bloccate:
        return {'scadenze': attuali, 'scadenze_nota': bloccate}
    if testata.tipo_documento == TestataFatturaAcquisto.TipoDocumento.TD04:
        return {'scadenze': [], 'scadenze_nota': 'Nota di credito: nessuna scadenza da pagare.'}
    scadenze_fs = ScadenzeAcquistoFormSet(post, instance=istanza, prefix='scadenze')
    if scadenze_fs.has_changed():
        manuali = sorted((
            {'data': f.cleaned_data['data_scadenza'], 'importo': f.cleaned_data['importo']}
            for f in scadenze_fs.forms
            if f.is_valid() and not f.cleaned_data.get('DELETE')
            and f.cleaned_data.get('data_scadenza') and f.cleaned_data.get('importo') is not None),
            key=lambda s: s['data'])
        somma = sum((s['importo'] for s in manuali), Decimal('0'))
        return {'scadenze': manuali, 'scadenze_manuali': True,
                'scadenze_differenza': valori['totale'] - somma if manuali else Decimal('0')}
    base_ora = (testata.forme_pagamento_id, testata.data_fornitore, valori['totale'])
    if attuali and base_ora == prima:
        return {'scadenze': attuali}
    if testata.forme_pagamento_id and testata.data_documento:
        generate = genera_scadenze_da_forma_pagamento(testata, totale=valori['totale'])
        if generate:
            return {'scadenze': generate}
    return {'scadenze': attuali}
