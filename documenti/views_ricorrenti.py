"""Vendite › Fatture ricorrenti: elenco, modello con le righe, storico delle
emissioni, «Emetti ora», sospendi/riattiva. La logica (periodi, emissione,
invio automatico) è in ``documenti/ricorrenti.py``."""
from __future__ import annotations

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.timezone import localdate
from django.views.decorators.http import require_POST

from accounts.permissions import require_perm

from . import ricorrenti
from .anteprima import _righe_valide, formset_con_iniziali
from .forms import FatturaRicorrenteForm, RigaFatturaRicorrenteFormSet
from .models import EmissioneRicorrente, FatturaRicorrente, TestataFattura
from .utils import azienda_per_calcoli, calcola_valori


def _con_righe(qs):
    return qs.select_related('cliente__posizione_iva').prefetch_related(
        'righe__iva', 'righe__articolo', 'emissioni')


@require_perm('documenti.vedi')
def ricorrente_list(request):
    oggi = localdate()
    azienda = azienda_per_calcoli()
    voci = []
    for r in _con_righe(FatturaRicorrente.objects.order_by('-attiva', 'nome')):
        da_emettere = ricorrenti.da_emettere(r, oggi)
        prossime = ricorrenti.prossime(r, oggi, 1)
        voci.append({
            'r': r,
            'totale': ricorrenti.valori_periodo(r, azienda=azienda)['totale'],
            'prossima': da_emettere[0] if da_emettere else (prossime[0] if prossime else None),
            'arretrati': len(da_emettere),
            'errori': sum(e.esito == EmissioneRicorrente.Esito.ERRORE for e in r.emissioni.all()),
            'stato': ricorrenti.stato(r, oggi),
        })
    return render(request, 'documenti/ricorrente_list.html', {
        'active_nav': 'fatture_ricorrenti', 'voci': voci, 'oggi': oggi,
    })


@require_perm('documenti.vedi')
def ricorrente_detail(request, pk):
    r = get_object_or_404(_con_righe(FatturaRicorrente.objects), pk=pk)
    oggi = localdate()
    emissioni = list(r.emissioni.select_related('fattura').order_by('-periodo'))
    for e in emissioni:
        e.etichetta = ricorrenti.periodo(r, e.periodo).etichetta
    righe = list(r.righe.all())
    prossime = ricorrenti.prossime(r, oggi, 3)
    # le descrizioni come usciranno nella prossima fattura
    esempio = prossime[0] if prossime else ricorrenti.periodo(r, oggi)
    for x in righe:
        x.descrizione_esempio = ricorrenti.segnaposto(x.descrizione_libera or '', esempio)
    return render(request, 'documenti/ricorrente_detail.html', {
        'active_nav': 'fatture_ricorrenti',
        'r': r,
        'righe': righe,
        'valori': ricorrenti.valori_periodo(r, righe),
        'da_emettere': ricorrenti.da_emettere(r, oggi),
        'prossime': prossime,
        'esempio': esempio,
        'emissioni': emissioni,
        'stato': ricorrenti.stato(r, oggi),
    })


def _render_form(request, form, righe_fs, r):
    return render(request, 'documenti/ricorrente_form.html', {
        'active_nav': 'fatture_ricorrenti', 'form': form, 'righe_formset': righe_fs,
        'r': r, 'is_create': not r.pk, 'segnaposto': ricorrenti.SEGNAPOSTO,
    })


def _iniziali_da_fattura(pk) -> tuple[dict, list[dict]] | None:
    """«Crea una ricorrenza da questa fattura»: cliente, pagamento, note e
    righe della fattura; la prima emissione è il periodo dopo la fattura
    (quella del mese corrente c'è già)."""
    f = TestataFattura.objects.filter(pk=pk).select_related('cliente').first()
    if f is None:
        return None
    base = f.data_documento or localdate()
    anno, mese = ricorrenti._sposta(base.year, base.month, 1)
    righe = list(f.righe.all())
    nome = (righe[0].descrizione_libera or '').strip().split('\n')[0][:80] if righe else ''
    testata = {
        'nome': nome or f'Fattura ricorrente {f.cliente}',
        'cliente': f.cliente_id, 'periodicita': 'mensile', 'giorno_emissione': base.day,
        'data_inizio': ricorrenti._giorno_in_mese(anno, mese, base.day),
        'tipo_documento': f.tipo_documento if f.tipo_documento != 'TD24' else 'TD01',
        'sezionale': f.sezionale, 'forme_pagamento': f.forme_pagamento_id,
        'conto_corrente': f.conto_corrente_id, 'note': f.note or '',
    }
    righe_iniziali = [{
        'numero_riga': i, 'articolo': x.articolo_id, 'descrizione_libera': x.descrizione_libera,
        'quantita': x.quantita, 'um': x.um, 'importo_unitario': x.importo_unitario, 'iva': x.iva_id,
    } for i, x in enumerate(righe, start=1)]
    return testata, righe_iniziali


@require_perm('documenti.modifica')
def ricorrente_nuova(request):
    r = FatturaRicorrente()
    if request.method != 'POST':
        copia = _iniziali_da_fattura(request.GET.get('da_fattura', '')) \
            if request.GET.get('da_fattura', '').isdigit() else None
        if copia:
            testata, righe = copia
            return _render_form(request, FatturaRicorrenteForm(initial=testata),
                                formset_con_iniziali(RigaFatturaRicorrenteFormSet, r, righe), r)
        iniziali = {}
        cliente = request.GET.get('cliente', '')
        if cliente.isdigit():
            iniziali['cliente'] = int(cliente)
        return _render_form(request, FatturaRicorrenteForm(initial=iniziali),
                            RigaFatturaRicorrenteFormSet(instance=r, prefix='righe'), r)
    return _salva(request, r)


@require_perm('documenti.modifica')
def ricorrente_modifica(request, pk):
    r = get_object_or_404(FatturaRicorrente, pk=pk)
    if request.method != 'POST':
        return _render_form(request, FatturaRicorrenteForm(instance=r),
                            RigaFatturaRicorrenteFormSet(instance=r, prefix='righe'), r)
    return _salva(request, r)


def _salva(request, r):
    nuova = not r.pk
    form = FatturaRicorrenteForm(request.POST, instance=r)
    righe_fs = RigaFatturaRicorrenteFormSet(request.POST, instance=r, prefix='righe')
    if not (form.is_valid() and righe_fs.is_valid()):
        return _render_form(request, form, righe_fs, r)
    if not any(f.has_changed() and not f.cleaned_data.get('DELETE') for f in righe_fs.forms) \
            and not (r.pk and r.righe.exists()):
        form.add_error(None, 'Aggiungi almeno una riga.')
        return _render_form(request, form, righe_fs, r)
    with transaction.atomic():
        r = form.save()
        righe_fs.save()
    arretrati = len(ricorrenti.da_emettere(r))
    testo = 'Fattura ricorrente creata.' if nuova else 'Fattura ricorrente aggiornata.'
    if arretrati:
        testo += (f' {arretrati} periodi già scaduti: si emettono alla prossima esecuzione '
                  f'(ogni mattina) o con «Emetti ora».')
    messages.success(request, testo)
    return redirect('documenti:ricorrente_detail', pk=r.pk)


@require_perm('documenti.elimina')
def ricorrente_elimina(request, pk):
    r = get_object_or_404(FatturaRicorrente, pk=pk)
    if request.method == 'POST':
        r.delete()
        messages.success(request, f'Fattura ricorrente «{r.nome}» eliminata. Le fatture già emesse restano.')
        return redirect('documenti:ricorrente_list')
    return render(request, 'documenti/confirm_delete.html', {
        'active_nav': 'fatture_ricorrenti', 'object': r,
        'object_label': f'la fattura ricorrente «{r.nome}»',
        'back_url': reverse('documenti:ricorrente_detail', args=[pk]),
    })


@require_perm('documenti.modifica')
@require_POST
def ricorrente_emetti(request, pk):
    """«Emetti ora i periodi scaduti»: come il timer, per questa ricorrenza."""
    r = get_object_or_404(FatturaRicorrente, pk=pk)
    emissioni = ricorrenti.emetti_scadute(r)
    ok = [e for e in emissioni if e.esito == EmissioneRicorrente.Esito.EMESSA]
    ko = [e for e in emissioni if e.esito == EmissioneRicorrente.Esito.ERRORE]
    if ok:
        numeri = ', '.join(f'{e.fattura.numero}/{e.fattura.anno}' for e in ok)
        messages.success(request, f'Emess{"a la fattura" if len(ok) == 1 else "e le fatture"} {numeri}.')
    for e in ko:
        messages.error(request, f'Periodo del {e.periodo:%d/%m/%Y} non emesso: {e.errore}')
    if not emissioni:
        messages.info(request, 'Nessun periodo da emettere.')
    return redirect('documenti:ricorrente_detail', pk=pk)


@require_perm('documenti.modifica')
@require_POST
def ricorrente_sospendi(request, pk):
    """Sospende o riattiva. Riattivando, i periodi passati durante la
    sospensione si saltano, a meno di chiedere di recuperarli."""
    r = get_object_or_404(FatturaRicorrente, pk=pk)
    r.attiva = not r.attiva
    r.save(update_fields=['attiva', 'updated_at'])
    if not r.attiva:
        messages.success(request, f'«{r.nome}» sospesa: non si emettono fatture finché non la riattivi.')
    elif request.POST.get('recupera') == '1':
        n = len(ricorrenti.da_emettere(r))
        messages.success(request, f'«{r.nome}» riattivata' + (
            f': {n} periodi arretrati da emettere.' if n else '.'))
    else:
        n = ricorrenti.salta_periodi(r)
        messages.success(request, f'«{r.nome}» riattivata' + (
            f': {n} periodi passati durante la sospensione saltati.' if n else '.'))
    return redirect('documenti:ricorrente_detail', pk=pk)


@require_perm('documenti.modifica')
def ricorrente_anteprima(request):
    """Riepilogo live del form (HTMX): totale di una fattura del periodo,
    prossime emissioni ed esempio di descrizione con i segnaposto."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    pk = request.POST.get('documento_pk', '')
    istanza = get_object_or_404(FatturaRicorrente, pk=pk) if pk.isdigit() else FatturaRicorrente()
    form = FatturaRicorrenteForm(request.POST, instance=istanza)
    form.is_valid()
    r = form.instance
    righe = _righe_valide(RigaFatturaRicorrenteFormSet(request.POST, instance=istanza, prefix='righe'))
    oggi = localdate()
    calendario_ok = not any(c in form.errors for c in ('periodicita', 'giorno_emissione', 'data_inizio',
                                                       'data_fine', 'competenza'))
    prossime, arretrati, esempio = [], 0, ''
    if calendario_ok and r.data_inizio:
        prossime = ricorrenti.prossime(r, oggi, 3) if r.attiva else []
        if r.pk:
            arretrati = len(ricorrenti.da_emettere(r, oggi))
        else:
            arretrati = len(ricorrenti.date_previste(r, oggi)) if r.attiva else 0
        p = prossime[0] if prossime else ricorrenti.periodo(r, max(oggi, r.data_inizio))
        testo = next((x.descrizione_libera for x in righe if x.descrizione_libera), '')
        esempio = ricorrenti.segnaposto(testo, p) if testo and '{' in testo else ''
    testata = TestataFattura(cliente=r.cliente if r.cliente_id else None,
                             tipo_documento=r.tipo_documento, data_documento=oggi)
    valori = calcola_valori(testata, ricorrenti.righe_documento(r, righe))
    return render(request, 'documenti/_riepilogo_ricorrente.html', {
        'tipo': 'ricorrente', 'valori': valori, 'n_righe': len(righe),
        'prossime': prossime, 'arretrati': arretrati, 'esempio': esempio, 'oggi': oggi,
        'senza_iva': [x for x in righe if x.iva_id is None] and not (r.cliente_id and r.cliente.posizione_iva_id),
    })

