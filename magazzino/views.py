"""
Vista aggregata ``Stock per articolo``.

Mostra per ogni articolo movimentato:
- ``carico`` (somma quantità tipo=C)
- ``scarico`` (somma tipo=S)
- ``impegnato`` (somma tipo=I, ovvero ordini da evadere)
- ``offerto`` (somma tipo=O)
- ``magazzino = carico - scarico``
- ``impegno = impegnato - scarico``  (quanto serve per evadere gli ordini)
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Case, DecimalField, F, Q, Sum, When, Value
from django.views.generic import ListView

from accounts.permissions import PermRequiredMixin
from anagrafiche.models import Articolo

from .models import Movimento


class StockListView(LoginRequiredMixin, PermRequiredMixin, ListView):
    required_perm = 'documenti.vedi'
    template_name = 'magazzino/stock_list.html'
    context_object_name = 'stock'
    paginate_by = 50

    def get_queryset(self):
        qs = (
            Articolo.objects
            .filter(movimenti__isnull=False)
            .distinct()
            .annotate(
                carico=Sum(
                    Case(
                        When(movimenti__tipo='C', then=F('movimenti__quantita')),
                        default=Value(0),
                        output_field=DecimalField(max_digits=12, decimal_places=2),
                    ),
                ),
                scarico=Sum(
                    Case(
                        When(movimenti__tipo='S', then=F('movimenti__quantita')),
                        default=Value(0),
                        output_field=DecimalField(max_digits=12, decimal_places=2),
                    ),
                ),
                impegnato=Sum(
                    Case(
                        When(movimenti__tipo='I', then=F('movimenti__quantita')),
                        default=Value(0),
                        output_field=DecimalField(max_digits=12, decimal_places=2),
                    ),
                ),
                offerto=Sum(
                    Case(
                        When(movimenti__tipo='O', then=F('movimenti__quantita')),
                        default=Value(0),
                        output_field=DecimalField(max_digits=12, decimal_places=2),
                    ),
                ),
            )
            .order_by('codice')
        )
        q = (self.request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(codice__icontains=q) | Q(descrizione__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'magazzino'
        ctx['q'] = self.request.GET.get('q', '')
        # Aggiungo campi derivati per ogni articolo (magazzino, impegno)
        stock_rows = []
        for a in ctx['stock']:
            carico = a.carico or Decimal('0')
            scarico = a.scarico or Decimal('0')
            impegnato = a.impegnato or Decimal('0')
            offerto = a.offerto or Decimal('0')
            stock_rows.append({
                'articolo': a,
                'carico': carico,
                'scarico': scarico,
                'impegnato': impegnato,
                'offerto': offerto,
                'magazzino': carico - scarico,
                'impegno': impegnato - scarico,
            })
        ctx['stock_rows'] = stock_rows
        # KPI header
        ctx['tot_articoli'] = Articolo.objects.filter(movimenti__isnull=False).distinct().count()
        ctx['tot_movimenti'] = Movimento.objects.count()
        return ctx
