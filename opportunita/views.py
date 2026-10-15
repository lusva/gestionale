from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import OuterRef, Q, Subquery
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, TemplateView, UpdateView

from accounts.permissions import PermRequiredMixin, require_perm
from attivita.models import Attivita

from .forms import OpportunitaForm
from .models import Opportunita, PIPELINE_COLUMNS, Stadio


COLUMN_META = {
    Stadio.NUOVA: ('Nuova', 'var(--ink-3)'),
    Stadio.QUALIFICATA: ('Qualificata', 'var(--info)'),
    Stadio.PROPOSTA: ('Proposta', 'var(--warn)'),
    Stadio.NEGOZIAZIONE: ('Negoziazione', 'var(--brand-violet)'),
    Stadio.CHIUSA_WIN: ('Vinte', 'var(--success)'),
    Stadio.CHIUSA_LOST: ('Perse', 'var(--danger)'),
}


def _pipeline_queryset():
    """Dati delle card e prima attività aperta in una sola query, anche sul dettaglio."""
    next_activity = Attivita.objects.filter(
        opportunita_id=OuterRef('pk'), completata=False,
    ).order_by('data', 'pk')
    return Opportunita.objects.select_related('cliente', 'owner__profile').annotate(
        next_attivita_id=Subquery(next_activity.values('pk')[:1]),
        next_attivita_titolo=Subquery(next_activity.values('titolo')[:1]),
        next_attivita_data=Subquery(next_activity.values('data')[:1]),
    )


def _filter_pipeline(queryset, params, user):
    query = params.get('q', '').strip()[:200]
    owner = params.get('owner', '')
    attention = params.get('attenzione', '')
    scope = params.get('scope', 'all')
    stage = params.get('stadio', '')
    if query:
        queryset = queryset.filter(Q(titolo__icontains=query) | Q(cliente__ragione_sociale__icontains=query))
    if stage in Stadio.values:
        queryset = queryset.filter(stadio=stage)
    else:
        stage = ''
    if owner == 'me':
        queryset = queryset.filter(owner=user)
    elif owner == 'unassigned':
        queryset = queryset.filter(owner__isnull=True)
    elif owner.isascii() and owner.isdigit() and len(owner) < 19:
        queryset = queryset.filter(owner_id=int(owner))
    else:
        owner = ''
    closed = (Stadio.CHIUSA_WIN, Stadio.CHIUSA_LOST)
    if scope == 'open':
        queryset = queryset.exclude(stadio__in=closed)
    elif scope == 'closed':
        queryset = queryset.filter(stadio__in=closed)
    else:
        scope = 'all'
    if attention in ('overdue', 'stale', 'no_followup'):
        queryset = queryset.exclude(stadio__in=closed)
        if attention == 'overdue':
            queryset = queryset.filter(chiusura_prevista__lt=timezone.localdate())
        elif attention == 'stale':
            queryset = queryset.filter(ingresso_stadio__lte=timezone.now() - timedelta(days=14))
        else:
            queryset = queryset.filter(next_attivita_id__isnull=True)
    else:
        attention = ''
    return queryset, {'q': query, 'owner': owner, 'attenzione': attention, 'scope': scope, 'stadio': stage}


def _pipeline_columns(queryset=None):
    if queryset is None:
        queryset = _pipeline_queryset()
    grouped = {stadio: [] for stadio in PIPELINE_COLUMNS}
    for opp in queryset.order_by('ordine', '-updated_at', 'pk'):
        grouped[opp.stadio].append(opp)
    cols = []
    for stadio in PIPELINE_COLUMNS:
        opps = grouped[stadio]
        total = sum((o.valore for o in opps), Decimal('0'))
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


def _pipeline_context(params, user):
    queryset, filters = _filter_pipeline(_pipeline_queryset(), params, user)
    cols = _pipeline_columns(queryset)
    opened = [opp for col in cols for opp in col['items'] if not opp.is_chiusa]
    return {
        'pipeline_cols': cols,
        'pipeline_total': sum((o.valore for o in opened), Decimal('0')),
        'pipeline_weighted': sum((o.valore * Decimal(o.probabilita) / 100 for o in opened), Decimal('0')),
        'pipeline_count': len(opened),
        'pipeline_visible_count': sum(c['count'] for c in cols),
        'pipeline_attention_count': sum(o.chiusura_in_ritardo or o.in_stallo or not o.next_attivita_id for o in opened),
        'pipeline_filters': filters,
        'pipeline_has_filters': any(filters[k] for k in ('q', 'owner', 'attenzione', 'stadio')) or filters['scope'] != 'all',
        'pipeline_now': timezone.now(),
    }


class PipelineView(LoginRequiredMixin, TemplateView):
    template_name = 'opportunita/pipeline.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_pipeline_context(self.request.GET, self.request.user))
        ctx['stadi'] = Stadio.choices
        ctx['pipeline_owners'] = get_user_model().objects.filter(
            Q(is_active=True) | Q(opportunita_owned__isnull=False),
        ).distinct().order_by('first_name', 'last_name', 'username')
        ctx['active_nav'] = 'opportunita'
        return ctx


class OpportunitaDetailView(LoginRequiredMixin, DetailView):
    model = Opportunita
    template_name = 'opportunita/detail.html'
    context_object_name = 'opp'

    def get_queryset(self):
        return _pipeline_queryset()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'opportunita'
        ctx['stadi'] = Stadio.choices
        ctx['pipeline_now'] = timezone.now()
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
        stadio = self.request.GET.get('stadio')
        if stadio in Stadio.values:
            initial['stadio'] = stadio
            if stadio in (Stadio.CHIUSA_WIN, Stadio.CHIUSA_LOST):
                initial['probabilita'] = 100 if stadio == Stadio.CHIUSA_WIN else 0
        initial['owner'] = self.request.user.pk
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
    opp = get_object_or_404(Opportunita.objects.select_for_update(), pk=pk)
    nuovo = request.POST.get('stadio')
    if nuovo not in dict(Stadio.choices):
        return HttpResponse(status=400)

    posizione_raw = request.POST.get('posizione')
    try:
        posizione = int(posizione_raw) if posizione_raw not in (None, '') else None
    except ValueError:
        posizione = None

    old_stadio = opp.stadio
    if nuovo == old_stadio and posizione is None:
        if request.headers.get('HX-Request') == 'true':
            return render(request, 'opportunita/_pipeline_results.html', _pipeline_context(request.GET, request.user))
        messages.info(request, 'L’opportunità è già nello stadio selezionato.')
        return redirect(opp)
    if nuovo != old_stadio:
        opp.cambia_stadio(nuovo)

    # Riassegna ordine: prendi gli ID nella colonna destinazione (escludendo opp)
    # e inserisci opp alla posizione richiesta.
    colonna = list(
        Opportunita.objects.select_for_update().filter(stadio=nuovo)
        .exclude(pk=opp.pk)
        .order_by('ordine', '-updated_at', 'pk'),
    )
    # La posizione del browser si riferisce alle sole card visibili. Mantieni
    # l'ordine relativo delle card nascoste quando la pipeline è filtrata.
    if posizione is not None:
        visible, _ = _filter_pipeline(_pipeline_queryset(), request.GET, request.user)
        visible_ids = list(visible.filter(stadio=nuovo).exclude(pk=opp.pk)
                           .order_by('ordine', '-updated_at', 'pk').values_list('pk', flat=True))
        posizione = max(posizione, 0)
        if posizione < len(visible_ids):
            anchor = visible_ids[posizione]
            posizione = next(i for i, item in enumerate(colonna) if item.pk == anchor)
        elif visible_ids:
            anchor = visible_ids[-1]
            posizione = next(i for i, item in enumerate(colonna) if item.pk == anchor) + 1
        else:
            posizione = len(colonna)
    else:
        posizione = len(colonna)
    colonna.insert(posizione, opp)
    changed = []
    for idx, item in enumerate(colonna):
        if item.ordine != idx:
            item.ordine = idx
            changed.append(item)
    if changed:
        Opportunita.objects.bulk_update(changed, ['ordine'])

    if request.headers.get('HX-Request') != 'true':
        messages.success(request, 'Stadio opportunità aggiornato.')
        return redirect(opp)
    return render(request, 'opportunita/_pipeline_results.html', _pipeline_context(request.GET, request.user))
