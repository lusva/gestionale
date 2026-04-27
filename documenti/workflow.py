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
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from accounts.permissions import has_perm

from .models import (
    RigaDocumento,
    TestataDdt,
    TestataFattura,
    TestataNotaCredito,
    TestataOfferta,
    TestataOrdine,
)


_RIGA_FIELDS = (
    'numero_riga', 'articolo_id', 'descrizione_libera',
    'importo_unitario', 'quantita', 'iva_id', 'um', 'colli',
    'prezzo_acquisto',
)


def _copia_righe(da_testata, attr_da, nuova_testata, attr_a):
    """Duplica le righe di ``da_testata`` collegandole a ``nuova_testata``."""
    sorgente = RigaDocumento.objects.filter(**{f'{attr_da}_id': da_testata.pk})
    for r in sorgente:
        kw = {f: getattr(r, f) for f in _RIGA_FIELDS}
        kw[f'{attr_a}_id'] = nuova_testata.pk
        RigaDocumento.objects.create(**kw)


def _ensure_perm(request):
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not has_perm(request.user, 'documenti.modifica'):
        messages.error(request, 'Permesso negato.')
        return redirect('documenti:fattura_list')
    return None


def _copia_testata_fields(src, dst, exclude=()):
    """Copia campi comuni tra testate (data_documento, cliente, sconti, ecc.)."""
    common = {
        'data_documento', 'data_registrazione', 'cliente_id', 'fornitore_id',
        'spese_imballo', 'spese_trasporto', 'sconto', 'sconto_incondizionato',
        'note', 'forme_pagamento_id', 'conto_corrente_id',
    }
    for field in common - set(exclude):
        if hasattr(src, field) and hasattr(dst, field):
            setattr(dst, field, getattr(src, field))


@require_POST
@transaction.atomic
def offerta_to_ordine(request, pk):
    err = _ensure_perm(request)
    if err is not None:
        return err
    offerta = get_object_or_404(TestataOfferta, pk=pk)
    ordine = TestataOrdine()
    _copia_testata_fields(offerta, ordine)
    ordine.stato = TestataOrdine.StatoOrdine.ATTESA
    ordine.save()  # auto-numera
    _copia_righe(offerta, 'testata_offerta', ordine, 'testata_ordine')
    offerta.ordine_collegato.add(ordine)
    TestataOfferta.objects.filter(pk=offerta.pk).update(
        stato=TestataOfferta.StatoOfferta.CONFERMATA,
    )
    messages.success(
        request,
        f'Ordine {ordine.numero}/{ordine.anno} creato dall\'offerta '
        f'{offerta.numero}/{offerta.anno}.',
    )
    return redirect('documenti:ordine_detail', pk=ordine.pk)


@require_POST
@transaction.atomic
def ordine_to_ddt(request, pk):
    err = _ensure_perm(request)
    if err is not None:
        return err
    ordine = get_object_or_404(TestataOrdine, pk=pk)
    ddt = TestataDdt()
    _copia_testata_fields(ordine, ddt)
    ddt.stato = TestataDdt.StatoDdt.CONFERMATO
    ddt.save()
    _copia_righe(ordine, 'testata_ordine', ddt, 'testata_ddt')
    ordine.ddt_collegato.add(ddt)
    messages.success(
        request,
        f'DDT {ddt.numero}/{ddt.anno} creato dall\'ordine '
        f'{ordine.numero}/{ordine.anno}.',
    )
    return redirect('documenti:ddt_detail', pk=ddt.pk)


@require_POST
@transaction.atomic
def ordine_to_fattura(request, pk):
    err = _ensure_perm(request)
    if err is not None:
        return err
    ordine = get_object_or_404(TestataOrdine, pk=pk)
    fattura = TestataFattura()
    _copia_testata_fields(ordine, fattura)
    fattura.tipo_documento = TestataFattura.TipoDocumento.TD01
    fattura.save()
    _copia_righe(ordine, 'testata_ordine', fattura, 'testata_fattura')
    ordine.fattura_collegata.add(fattura)
    messages.success(
        request,
        f'Fattura {fattura.numero}/{fattura.anno} creata dall\'ordine '
        f'{ordine.numero}/{ordine.anno}.',
    )
    return redirect('documenti:fattura_detail', pk=fattura.pk)


@require_POST
@transaction.atomic
def fattura_to_nota_credito(request, pk):
    err = _ensure_perm(request)
    if err is not None:
        return err
    fattura = get_object_or_404(TestataFattura, pk=pk)
    nc = TestataNotaCredito()
    _copia_testata_fields(fattura, nc)
    nc.tipo_documento = TestataNotaCredito.TipoDocumento.TD04
    nc.save()
    _copia_righe(fattura, 'testata_fattura', nc, 'testata_nota_credito')
    fattura.nota_credito_collegata.add(nc)
    messages.success(
        request,
        f'Nota di credito {nc.numero}/{nc.anno} creata dalla fattura '
        f'{fattura.numero}/{fattura.anno}.',
    )
    return redirect('documenti:nota_credito_detail', pk=nc.pk)
