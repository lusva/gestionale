"""Aiuti per i form dei documenti (fattura, offerta, ordine, DDT, nota di
credito, DDT fornitore): anteprima dei totali mentre si compila, dati
predefiniti dell'articolo scelto in una riga, copia di un documento
esistente come base di uno nuovo («Duplica»).

L'anteprima usa gli stessi pezzi del salvataggio — i form, ``calcola_valori``
(gli stessi totali di PDF e FatturaPA) e le scadenze dalla forma di
pagamento — ma non scrive niente.
"""
from __future__ import annotations

from decimal import Decimal

from django.http import Http404, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, render

from accounts.permissions import require_perm

from .utils import calcola_valori, genera_scadenze_da_forma_pagamento


def registro():
    """tipo → (modello testata, form testata, formset righe, campo FK della riga)."""
    from .doc_forms import (
        RigaDdtFormSet, RigaDdtFornitoreFormSet, RigaNotaCreditoFormSet,
        RigaOffertaFormSet, RigaOrdineFormSet, TestataDdtForm, TestataDdtFornitoreForm,
        TestataNotaCreditoForm, TestataOffertaForm, TestataOrdineForm,
    )
    from .forms import RigaFatturaFormSet, TestataFatturaForm
    from .models import (
        TestataDdt, TestataDdtFornitore, TestataFattura, TestataNotaCredito,
        TestataOfferta, TestataOrdine,
    )
    return {
        'fattura': (TestataFattura, TestataFatturaForm, RigaFatturaFormSet, 'testata_fattura'),
        'offerta': (TestataOfferta, TestataOffertaForm, RigaOffertaFormSet, 'testata_offerta'),
        'ordine': (TestataOrdine, TestataOrdineForm, RigaOrdineFormSet, 'testata_ordine'),
        'ddt': (TestataDdt, TestataDdtForm, RigaDdtFormSet, 'testata_ddt'),
        'nota_credito': (TestataNotaCredito, TestataNotaCreditoForm, RigaNotaCreditoFormSet,
                         'testata_nota_credito'),
        'ddt_fornitore': (TestataDdtFornitore, TestataDdtFornitoreForm, RigaDdtFornitoreFormSet,
                          'testata_ddt_fornitore'),
    }


def _righe_valide(formset):
    """Le righe del formset come oggetti non salvati: solo quelle compilate
    e con i campi validi, senza quelle segnate da eliminare. Gli errori del
    modello si ignorano: in anteprima la testata può non essere salvata e
    ``RigaDocumento.clean`` la pretende."""
    righe = []
    for f in formset.forms:
        f.is_valid()                       # cleaned_data e instance compilati
        if any(campo != '__all__' for campo in f.errors):
            continue
        if not f.has_changed() and not f.instance.pk:
            continue                       # riga vuota in fondo al form
        if f.cleaned_data.get('DELETE'):
            continue
        if f.instance.quantita is None:
            continue
        righe.append(f.instance)
    return righe


@require_perm('documenti.modifica')
def documento_anteprima(request, tipo):
    """POST con i dati del form: riepilogo di totali e scadenze come
    risulterebbero salvando (HTMX, frammento ``_riepilogo_documento.html``)."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    voce = registro().get(tipo)
    if voce is None:
        raise Http404
    model, form_class, formset_class, _ = voce
    pk = request.POST.get('documento_pk', '')
    istanza = get_object_or_404(model, pk=pk) if pk.isdigit() else model()
    if tipo == 'fattura':
        from .servizi import base_scadenze
        prima = base_scadenze(istanza) if istanza.pk else None   # prima che il form la tocchi

    form = form_class(request.POST, instance=istanza)
    form.is_valid()                        # riempie form.instance con i campi validi
    testata = form.instance
    formset = formset_class(request.POST, instance=istanza, prefix='righe')
    righe = _righe_valide(formset)
    valori = calcola_valori(testata, righe)

    ctx = {'tipo': tipo, 'valori': valori, 'n_righe': len(righe)}
    if tipo == 'fattura':
        ctx.update(_scadenze_previste(request.POST, istanza, testata, valori, prima))
    return render(request, 'documenti/_riepilogo_documento.html', ctx)


def _scadenze_previste(post, istanza, testata, valori, prima) -> dict:
    """Le scadenze che avrà la fattura salvata, con la stessa regola del
    salvataggio (views._maybe_genera_scadenze_auto → servizi.sincronizza_scadenze):
    quelle del form se l'utente le ha toccate; quelle attuali se forma di
    pagamento, data e totale non cambiano (o se è pagata o già al SDI);
    altrimenti quelle della forma di pagamento."""
    from .forms import ScadenzeFatturaFormSet
    from .servizi import ha_incassi
    if ha_incassi(istanza):
        return {'scadenze': list(istanza.scadenze.values('data', 'importo')),
                'scadenze_nota': 'Ci sono incassi registrati: le scadenze restano queste.'}
    scadenze_fs = ScadenzeFatturaFormSet(post, instance=istanza, prefix='scadenze')
    if scadenze_fs.has_changed():
        manuali = sorted((
            {'data': f.cleaned_data['data'], 'importo': f.cleaned_data['importo']}
            for f in scadenze_fs.forms
            if f.is_valid() and not f.cleaned_data.get('DELETE')
            and f.cleaned_data.get('data') and f.cleaned_data.get('importo') is not None),
            key=lambda s: s['data'])
        somma = sum((s['importo'] for s in manuali), Decimal('0'))
        return {'scadenze': manuali, 'scadenze_manuali': True,
                'scadenze_differenza': valori['totale'] - somma if manuali else Decimal('0')}
    attuali = list(istanza.scadenze.values('data', 'importo')) if istanza.pk else []
    base_ora = (testata.forme_pagamento_id, testata.data_documento, valori['totale'])
    if attuali and (testata.pagata or testata.emessa_sdi or base_ora == prima):
        return {'scadenze': attuali}
    if testata.forme_pagamento_id and testata.data_documento:
        generate = genera_scadenze_da_forma_pagamento(testata, totale=valori['totale'])
        if generate:
            return {'scadenze': generate}
    return {'scadenze': attuali}


@require_perm('documenti.vedi')
def articolo_predefiniti(request, pk):
    """Descrizione, prezzo di listino, unità di misura e IVA di un articolo,
    per precompilare la riga in cui lo si sceglie."""
    from anagrafiche.models import Articolo
    from .models import RigaDocumento
    a = get_object_or_404(Articolo, pk=pk)
    um = a.um if a.um in RigaDocumento.UM.values else ''
    return JsonResponse({
        'descrizione': a.descrizione or '',
        'prezzo': str(a.prezzo_listino) if a.prezzo_listino else '',
        'um': um,
        'iva': a.posizione_iva_id,
    })


# Campi copiati da un documento all'altro con «Duplica»: le date no (il nuovo
# documento nasce oggi), stati, pagamento e SDI nemmeno.
_CAMPI_TESTATA = (
    'cliente', 'fornitore', 'forme_pagamento', 'conto_corrente',
    'spese_imballo', 'spese_trasporto', 'sconto', 'sconto_incondizionato', 'note',
    'tipo_documento', 'sezionale',
    'numero_palette', 'peso_netto', 'peso_lordo', 'cura', 'causale_trasporto', 'porto',
    'aspetto_beni', 'vettore', 'destinazione_merce',
)
_CAMPI_RIGA = ('numero_riga', 'articolo', 'descrizione_libera', 'quantita', 'um',
               'importo_unitario', 'iva')


def iniziali_da_copia(tipo: str, pk, form_class) -> tuple[dict, list[dict]] | None:
    """Valori iniziali (testata, righe) di un form nuovo che copia il
    documento ``pk`` dello stesso tipo; None se non esiste."""
    voce = registro().get(tipo)
    if voce is None or not str(pk).isdigit():
        return None
    model, _, _, fk = voce
    origine = model.objects.filter(pk=pk).first()
    if origine is None:
        return None
    campi_form = set(form_class.base_fields)
    testata = {}
    for campo in _CAMPI_TESTATA:
        if campo in campi_form and hasattr(origine, f'{campo}_id'):
            testata[campo] = getattr(origine, f'{campo}_id')
        elif campo in campi_form and hasattr(origine, campo):
            testata[campo] = getattr(origine, campo)
    righe = []
    for i, r in enumerate(origine.righe.all(), start=1):
        riga = {c: getattr(r, f'{c}_id') if c in ('articolo', 'iva') else getattr(r, c)
                for c in _CAMPI_RIGA}
        riga['numero_riga'] = i
        righe.append(riga)
    return testata, righe


def formset_con_iniziali(formset_class, instance, righe: list[dict]):
    """Formset righe non legato, con una riga precompilata per ogni voce di
    ``righe`` (più una vuota). Le righe precompilate si salvano: al POST il
    formset si ricostruisce senza ``initial``, quindi risultano compilate."""
    formset = formset_class(instance=instance, prefix='righe', initial=righe)
    formset.extra = len(righe) + 1
    return formset
