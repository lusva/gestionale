from datetime import datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import (
    CreateView, DeleteView, DetailView, ListView, UpdateView,
)

from accounts.permissions import PermRequiredMixin, has_perm
from opportunita.models import Opportunita

from .forms import AttivitaForm
from .models import Attivita, TipoAttivita


def _agenda_dates():
    now = timezone.now()
    today = timezone.localdate(now)
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(today, time.min), tz)
    tomorrow = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min), tz)
    week_end = timezone.make_aware(datetime.combine(today + timedelta(days=7), time.min), tz)
    return now, start, tomorrow, week_end


class AttivitaListView(LoginRequiredMixin, ListView):
    model = Attivita
    template_name = 'attivita/list.html'
    context_object_name = 'attivita_list'
    paginate_by = 20

    def get_queryset(self):
        self.now, self.today_start, self.tomorrow, self.week_end = _agenda_dates()
        params = self.request.GET
        self.stato = params.get('stato', 'da_fare')
        if self.stato not in {'tutte', 'da_fare', 'completate'}:
            self.stato = 'da_fare'
        self.tipo = params.get('tipo', '')
        if self.tipo not in TipoAttivita.values:
            self.tipo = ''
        self.periodo = params.get('periodo', '')
        if self.periodo not in {'scadute', 'oggi', 'settimana'}:
            self.periodo = ''
        self.owner = 'me' if params.get('owner') == 'me' else ''
        self.search = params.get('q', '').strip()[:200]

        qs = Attivita.objects.select_related('cliente', 'opportunita', 'owner__profile')
        if self.search:
            qs = qs.filter(
                Q(titolo__icontains=self.search) | Q(descrizione__icontains=self.search)
                | Q(cliente__ragione_sociale__icontains=self.search)
                | Q(opportunita__titolo__icontains=self.search)
            )
        if self.tipo:
            qs = qs.filter(tipo=self.tipo)
        if self.owner:
            qs = qs.filter(owner=self.request.user)
        # Il riepilogo mantiene ricerca/tipo/assegnatario e permette di spostarsi
        # tra periodi senza far sparire il carico di lavoro ancora aperto.
        self.summary_queryset = qs
        if self.stato == 'da_fare':
            qs = qs.filter(completata=False)
        elif self.stato == 'completate':
            qs = qs.filter(completata=True)
        if self.periodo == 'scadute':
            qs = qs.filter(completata=False, data__lt=self.now)
        elif self.periodo == 'oggi':
            qs = qs.filter(data__gte=self.today_start, data__lt=self.tomorrow)
        elif self.periodo == 'settimana':
            qs = qs.filter(data__gte=self.today_start, data__lt=self.week_end)
        if self.stato == 'completate':
            return qs.order_by('-data', '-pk')
        return qs.order_by('completata', 'data', 'pk')

    def paginate_queryset(self, queryset, page_size):
        # Dopo il completamento dell'ultima riga, torna alla pagina disponibile
        # precedente invece di restituire 404 al frammento HTMX.
        paginator = self.get_paginator(queryset, page_size)
        number = self.request.GET.get('page', 1)
        page = paginator.get_page(paginator.num_pages if number == 'last' else number)
        return paginator, page, page.object_list, page.has_other_pages()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        open_activities = Q(completata=False)
        ctx.update({
            'active_nav': 'attivita',
            'tipi_choices': TipoAttivita.choices,
            'stato_attivo': self.stato,
            'tipo_attivo': self.tipo,
            'periodo_attivo': self.periodo,
            'owner_attivo': self.owner,
            'search_query': self.search,
            'has_filters': bool(self.search or self.tipo or self.periodo or self.owner
                                or self.stato != 'da_fare'),
            'can_edit': has_perm(self.request.user, 'attivita.modifica'),
            'now': self.now,
            'today': timezone.localdate(self.now),
            'stats': self.summary_queryset.aggregate(
                aperte=Count('pk', filter=open_activities),
                scadute=Count('pk', filter=open_activities & Q(data__lt=self.now)),
                oggi=Count('pk', filter=open_activities & Q(
                    data__gte=self.today_start, data__lt=self.tomorrow)),
                settimana=Count('pk', filter=open_activities & Q(
                    data__gte=self.today_start, data__lt=self.week_end)),
            ),
        })
        params = self.request.GET.copy()
        if 'page' in params:
            params['page'] = str(ctx['page_obj'].number)
        ctx['list_query'] = params.urlencode()
        ctx['return_url'] = reverse('attivita:list')
        if ctx['list_query']:
            ctx['return_url'] += '?' + ctx['list_query']
        return ctx


class AttivitaDetailView(LoginRequiredMixin, DetailView):
    model = Attivita
    template_name = 'attivita/detail.html'
    context_object_name = 'attivita'
    queryset = Attivita.objects.select_related('cliente', 'opportunita', 'owner')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'attivita'
        ctx['can_edit'] = has_perm(self.request.user, 'attivita.modifica')
        ctx['now'] = timezone.now()
        return ctx


class AttivitaCreateView(PermRequiredMixin, LoginRequiredMixin, SuccessMessageMixin, CreateView):
    required_perm = 'attivita.modifica'
    model = Attivita
    form_class = AttivitaForm
    template_name = 'attivita/form.html'
    success_url = reverse_lazy('attivita:list')
    success_message = 'Attività creata e aggiunta all’agenda.'

    def get_initial(self):
        initial = super().get_initial()
        initial['owner'] = self.request.user.pk
        initial['data'] = timezone.localtime().replace(second=0, microsecond=0)
        # "Assegna attività" dalla pagina Team: ?owner=<id utente>.
        for field in ('owner', 'cliente'):
            value = self.request.GET.get(field, '')
            if value.isascii() and value.isdigit() and len(value) < 19:
                initial[field] = int(value)
        opportunita_id = self.request.GET.get('opportunita', '')
        if opportunita_id.isascii() and opportunita_id.isdigit() and len(opportunita_id) < 19:
            opportunita = Opportunita.objects.filter(pk=int(opportunita_id)).first()
            if opportunita:
                initial['opportunita'] = opportunita.pk
                initial['cliente'] = opportunita.cliente_id
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'attivita'
        ctx['is_create'] = True
        return ctx


class AttivitaUpdateView(PermRequiredMixin, LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    required_perm = 'attivita.modifica'
    model = Attivita
    form_class = AttivitaForm
    template_name = 'attivita/form.html'
    success_url = reverse_lazy('attivita:list')
    success_message = 'Attività aggiornata.'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'attivita'
        ctx['is_create'] = False
        return ctx


class AttivitaDeleteView(PermRequiredMixin, LoginRequiredMixin, DeleteView):
    required_perm = 'attivita.modifica'
    model = Attivita
    success_url = reverse_lazy('attivita:list')
    template_name = 'attivita/confirm_delete.html'

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Attività eliminata.')
        return response


@login_required
@require_POST
def toggle_completata(request, pk):
    if not has_perm(request.user, 'attivita.modifica'):
        return HttpResponse(status=403)
    # Mantiene il segnale post_save (e quindi l'audit con l'attore corrente)
    # all'interno della stessa transazione che protegge il cambio di stato.
    with transaction.atomic():
        att = get_object_or_404(Attivita.objects.select_for_update(), pk=pk)
        att.completata = not att.completata
        att.save(update_fields=['completata'])
    status_message = 'Attività completata.' if att.completata else 'Attività riaperta.'
    if getattr(request, 'htmx', None):
        view = AttivitaListView()
        view.setup(request)
        view.object_list = view.get_queryset()
        context = view.get_context_data()
        context['status_message'] = status_message
        response = render(request, 'attivita/_results.html', context)
        response['HX-Trigger'] = 'crm:changed'
        return response
    messages.success(request, status_message)
    next_url = request.POST.get('next', '')
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ):
        next_url = reverse('attivita:list')
    return redirect(next_url)
