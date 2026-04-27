"""API JSON read-only.

Endpoint coperti:
    GET  /api/v1/health               → {"status": "ok", "user": "..."}
    GET  /api/v1/clienti              → {"results": [...], "next_page": N|null}
    GET  /api/v1/clienti/<pk>         → cliente singolo
    GET  /api/v1/opportunita          → lista
    GET  /api/v1/opportunita/<pk>     → singola
    GET  /api/v1/attivita             → lista

Paginazione: `?page=N&page_size=K` (page_size max 100).
"""
from __future__ import annotations

from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from attivita.models import Attivita
from clienti.models import Cliente
from opportunita.models import Opportunita

from .auth import token_auth_required
from .serializers import (
    attivita_to_dict, cliente_to_dict, opportunita_to_dict,
)


def _paginate(request, qs):
    try:
        page = max(1, int(request.GET.get('page') or 1))
    except ValueError:
        page = 1
    try:
        page_size = min(100, max(1, int(request.GET.get('page_size') or 25)))
    except ValueError:
        page_size = 25
    paginator = Paginator(qs, page_size)
    page_obj = paginator.page(min(page, paginator.num_pages or 1))
    return page_obj, paginator


@require_GET
@token_auth_required
def health(request):
    return JsonResponse({
        'status': 'ok',
        'user': request.api_user.email,
        'version': 'v1',
    })


@require_GET
@token_auth_required
def clienti_list(request):
    qs = Cliente.objects.select_related('settore').order_by('-updated_at')
    q = (request.GET.get('q') or '').strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(
            Q(ragione_sociale__icontains=q) | Q(partita_iva__icontains=q),
        )
    page_obj, paginator = _paginate(request, qs)
    return JsonResponse({
        'results': [cliente_to_dict(c) for c in page_obj.object_list],
        'count': paginator.count,
        'page': page_obj.number,
        'next_page': page_obj.next_page_number() if page_obj.has_next() else None,
    })


@require_GET
@token_auth_required
def cliente_detail(request, pk):
    c = get_object_or_404(Cliente.objects.select_related('settore'), pk=pk)
    return JsonResponse(cliente_to_dict(c))


@require_GET
@token_auth_required
def opportunita_list(request):
    qs = Opportunita.objects.select_related('cliente', 'owner').order_by('-updated_at')
    stadio = request.GET.get('stadio')
    if stadio:
        qs = qs.filter(stadio=stadio)
    page_obj, paginator = _paginate(request, qs)
    return JsonResponse({
        'results': [opportunita_to_dict(o) for o in page_obj.object_list],
        'count': paginator.count,
        'page': page_obj.number,
        'next_page': page_obj.next_page_number() if page_obj.has_next() else None,
    })


@require_GET
@token_auth_required
def opportunita_detail(request, pk):
    o = get_object_or_404(
        Opportunita.objects.select_related('cliente', 'owner'), pk=pk,
    )
    return JsonResponse(opportunita_to_dict(o))


@require_GET
@token_auth_required
def attivita_list(request):
    qs = Attivita.objects.select_related('cliente', 'owner').order_by('-data')
    page_obj, paginator = _paginate(request, qs)
    return JsonResponse({
        'results': [attivita_to_dict(a) for a in page_obj.object_list],
        'count': paginator.count,
        'page': page_obj.number,
        'next_page': page_obj.next_page_number() if page_obj.has_next() else None,
    })
