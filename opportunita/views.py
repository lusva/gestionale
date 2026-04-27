from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, TemplateView, UpdateView

from accounts.permissions import PermRequiredMixin, require_perm

from .forms import OpportunitaForm
from .models import Opportunita, PIPELINE_COLUMNS, Stadio


COLUMN_META = {
    Stadio.NUOVA: ('Nuova', 'var(--ink-3)'),
    Stadio.QUALIFICATA: ('Qualificata', 'var(--info)'),
    Stadio.PROPOSTA: ('Proposta', 'var(--warn)'),
    Stadio.NEGOZIAZIONE: ('Negoziazione', 'var(--brand-violet)'),
    Stadio.CHIUSA_WIN: ('Chiusa', 'var(--success)'),
}


def _pipeline_columns():
    cols = []
    for stadio in PIPELINE_COLUMNS:
        opps = list(
            Opportunita.objects.filter(stadio=stadio)
            .order_by('ordine', '-updated_at')
            .select_related('cliente', 'owner__profile'),
        )
        total = sum(o.valore for o in opps)
        label, color = COLUMN_META[stadio]
        cols.append({
            'id': stadio,
            'label': label,
            'color': color,
            'items': opps,
            'count': len(opps),
            'total': total,
        })
    return cols


class PipelineView(LoginRequiredMixin, TemplateView):
    template_name = 'opportunita/pipeline.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        cols = _pipeline_columns()
        ctx['pipeline_cols'] = cols
        ctx['pipeline_total'] = sum(c['total'] for c in cols)
        ctx['pipeline_count'] = sum(c['count'] for c in cols)
        ctx['active_nav'] = 'opportunita'
        return ctx


class OpportunitaDetailView(LoginRequiredMixin, DetailView):
    model = Opportunita
    template_name = 'opportunita/detail.html'
    context_object_name = 'opp'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'opportunita'
        return ctx


class OpportunitaCreateView(PermRequiredMixin, LoginRequiredMixin, CreateView):
    required_perm = 'opportunita.modifica'
    model = Opportunita
    form_class = OpportunitaForm
    template_name = 'opportunita/form.html'

    def get_initial(self):
        initial = super().get_initial()
        cliente_id = self.request.GET.get('cliente')
        if cliente_id:
            initial['cliente'] = cliente_id
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'opportunita'
        ctx['is_create'] = True
        return ctx

    def form_valid(self, form):
        messages.success(self.request, 'Opportunità creata')
        return super().form_valid(form)


class OpportunitaUpdateView(PermRequiredMixin, LoginRequiredMixin, UpdateView):
    required_perm = 'opportunita.modifica'
    model = Opportunita
    form_class = OpportunitaForm
    template_name = 'opportunita/form.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'opportunita'
        ctx['is_create'] = False
        return ctx


class OpportunitaDeleteView(PermRequiredMixin, LoginRequiredMixin, DeleteView):
    required_perm = 'opportunita.elimina'
    model = Opportunita
    success_url = reverse_lazy('opportunita:list')
    template_name = 'opportunita/confirm_delete.html'


@require_POST
@require_perm('opportunita.modifica')
@transaction.atomic
def sposta_stadio(request, pk):
    """HTMX endpoint to move an opportunity to a different stage and position.

    Expects POST fields:
        stadio   — target Stadio value (required)
        posizione — 0-based index within the destination column (optional,
                    default = append in coda)

    Returns the full pipeline markup.
    """
    opp = get_object_or_404(Opportunita, pk=pk)
    nuovo = request.POST.get('stadio')
    if nuovo not in dict(Stadio.choices):
        return HttpResponse(status=400)

    posizione_raw = request.POST.get('posizione')
    try:
        posizione = int(posizione_raw) if posizione_raw not in (None, '') else None
    except ValueError:
        posizione = None

    old_stadio = opp.stadio
    if nuovo != old_stadio:
        opp.cambia_stadio(nuovo)

    # Riassegna ordine: prendi gli ID nella colonna destinazione (escludendo opp)
    # e inserisci opp alla posizione richiesta.
    colonna = list(
        Opportunita.objects.filter(stadio=nuovo)
        .exclude(pk=opp.pk)
        .order_by('ordine', '-updated_at')
        .values_list('pk', flat=True),
    )
    if posizione is None or posizione > len(colonna):
        posizione = len(colonna)
    colonna.insert(max(posizione, 0), opp.pk)
    for idx, opp_pk in enumerate(colonna):
        Opportunita.objects.filter(pk=opp_pk).update(ordine=idx)

    cols = _pipeline_columns()
    ctx = {
        'pipeline_cols': cols,
        'pipeline_total': sum(c['total'] for c in cols),
        'pipeline_count': sum(c['count'] for c in cols),
    }
    return render(request, 'opportunita/_pipeline_board.html', ctx)
