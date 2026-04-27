from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import (
    CreateView, DeleteView, DetailView, ListView, UpdateView,
)

from .forms import AttivitaForm
from .models import Attivita, TipoAttivita


class AttivitaListView(LoginRequiredMixin, ListView):
    model = Attivita
    template_name = 'attivita/list.html'
    context_object_name = 'attivita_list'
    paginate_by = 20

    def get_queryset(self):
        qs = Attivita.objects.select_related('cliente', 'opportunita', 'owner__profile')
        stato = self.request.GET.get('stato')
        if stato == 'da_fare':
            qs = qs.filter(completata=False)
        elif stato == 'completate':
            qs = qs.filter(completata=True)
        tipo = self.request.GET.get('tipo')
        if tipo:
            qs = qs.filter(tipo=tipo)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'attivita'
        ctx['tipi_choices'] = TipoAttivita.choices
        ctx['stato_attivo'] = self.request.GET.get('stato', 'tutte')
        ctx['tipo_attivo'] = self.request.GET.get('tipo', '')
        return ctx


class AttivitaDetailView(LoginRequiredMixin, DetailView):
    model = Attivita
    template_name = 'attivita/detail.html'
    context_object_name = 'attivita'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'attivita'
        return ctx


class AttivitaCreateView(LoginRequiredMixin, CreateView):
    model = Attivita
    form_class = AttivitaForm
    template_name = 'attivita/form.html'
    success_url = reverse_lazy('attivita:list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'attivita'
        ctx['is_create'] = True
        return ctx


class AttivitaUpdateView(LoginRequiredMixin, UpdateView):
    model = Attivita
    form_class = AttivitaForm
    template_name = 'attivita/form.html'
    success_url = reverse_lazy('attivita:list')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['active_nav'] = 'attivita'
        ctx['is_create'] = False
        return ctx


class AttivitaDeleteView(LoginRequiredMixin, DeleteView):
    model = Attivita
    success_url = reverse_lazy('attivita:list')
    template_name = 'attivita/confirm_delete.html'


@require_POST
def toggle_completata(request, pk):
    att = get_object_or_404(Attivita, pk=pk)
    att.completata = not att.completata
    att.save(update_fields=['completata'])
    if getattr(request, 'htmx', None):
        return render(request, 'attivita/_row.html', {'a': att})
    return HttpResponse(status=204)
