"""
Transizioni tra documenti del ciclo attivo.

- ``offerta_to_ordine``: crea TestataOrdine copiando le righe, lega via M2M
  ``offerta.ordine_collegato``, marca l'offerta come CONFERMATA.
- ``ordine_to_ddt``: crea TestataDdt copiando le righe, lega via M2M
  ``ordine.ddt_collegato``.
- ``ordine_to_fattura``: crea TestataFattura copiando le righe, lega via
  M2M ``ordine.fattura_collegata``.
- ``fattura_to_nota_credito``: crea TestataNotaCredito (TD04) copiando le
  righe e lega via M2M ``fattura.nota_credito_collegata``.

Tutte le transizioni sono atomiche e coperte dal permesso
``documenti.modifica``.
"""
from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from accounts.permissions import has_perm

from . import servizi
from .models import TestataFattura, TestataOfferta, TestataOrdine


def _ensure_perm(request):
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not has_perm(request.user, 'documenti.modifica'):
        messages.error(request, 'Permesso negato.')
        return redirect('documenti:fattura_list')
    return None


def _transizione(request, model, pk, verso, nome_nuovo, url_nuovo, url_origine):
    """La vista di una transizione: la logica sta in ``servizi.converti``,
    la stessa usata dall'API e dal server MCP."""
    err = _ensure_perm(request)
    if err is not None:
        return err
    origine = get_object_or_404(model, pk=pk)
    try:
        nuovo = servizi.converti(verso, origine)
    except servizi.DatiNonValidi as exc:
        # già convertito, annullato, doppio click…
        messages.error(request, ' '.join(exc.errori.values()))
        return redirect(url_origine, pk=pk)
    messages.success(
        request,
        f'{nome_nuovo} {nuovo.numero}/{nuovo.anno} creato da '
        f'{origine.numero}/{origine.anno}.',
    )
    return redirect(url_nuovo, pk=nuovo.pk)


@require_POST
def offerta_to_ordine(request, pk):
    return _transizione(request, TestataOfferta, pk, 'offerta_in_ordine', 'Ordine',
                        'documenti:ordine_detail', 'documenti:offerta_detail')


@require_POST
def ordine_to_ddt(request, pk):
    return _transizione(request, TestataOrdine, pk, 'ordine_in_ddt', 'DDT',
                        'documenti:ddt_detail', 'documenti:ordine_detail')


@require_POST
def ordine_to_fattura(request, pk):
    return _transizione(request, TestataOrdine, pk, 'ordine_in_fattura', 'Fattura',
                        'documenti:fattura_detail', 'documenti:ordine_detail')


@require_POST
def fattura_to_nota_credito(request, pk):
    return _transizione(request, TestataFattura, pk, 'fattura_in_nota_credito',
                        'Nota di credito', 'documenti:nota_credito_detail',
                        'documenti:fattura_detail')
