"""Note di credito verso il SDI: l'XML FatturaPA (TD04) e l'invio, come per
le fatture (``invio.invia_al_sdi``, ``fatt_el.build_documento_xml``). Il
resto (lista, dettaglio, form) sono le view generiche di ``doc_views``."""
from __future__ import annotations

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from accounts.permissions import require_perm

from .models import TestataNotaCredito


@require_perm('documenti.vedi')
def nota_credito_xml(request, pk):
    from anagrafiche.models import AnagraficaAzienda
    from .fatt_el import build_documento_xml, nome_file_sdi

    nota = get_object_or_404(TestataNotaCredito, pk=pk)
    try:
        xml_bytes = build_documento_xml(nota)
    except ValueError as exc:  # anche FatturaElettronicaError
        messages.error(request, f'FatturaPA non generabile: {exc}')
        return redirect('documenti:nota_credito_detail', pk=pk)
    az = AnagraficaAzienda.objects.first()
    risposta = HttpResponse(xml_bytes, content_type='application/xml; charset=utf-8')
    risposta['Content-Disposition'] = f'attachment; filename="{nome_file_sdi(nota, az.partita_iva if az else "")}"'
    return risposta


@require_perm('documenti.modifica')
@require_POST
def nota_credito_invia_sdi(request, pk):
    from .invio import invia_al_sdi

    esito = invia_al_sdi(get_object_or_404(TestataNotaCredito, pk=pk))
    livello = {'success': messages.SUCCESS, 'warning': messages.WARNING}.get(esito.livello, messages.ERROR)
    messages.add_message(request, livello, esito.messaggio)
    return redirect('documenti:nota_credito_detail', pk=pk)
