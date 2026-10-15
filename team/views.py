"""Pagina Team: chi c'è in squadra, cosa ha in carico e chi è collegato.

Le card usano dati che il gestionale ha già: le attività assegnate
(`Attivita.owner`), i clienti seguiti (`Cliente.account_manager`), le
opportunità aperte (`Opportunita.owner`) e la scheda del profilo.
"""
import datetime as dt

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Profile, Ruolo
from accounts.permissions import has_perm
from accounts.tenancy import utenti_azienda
from tenancy.context import current_azienda_id
from attivita.models import Attivita
from clienti.models import Cliente
from opportunita.models import Opportunita, Stadio

from .forms import SchedaForm

# Carico = durata delle attività aperte con data entro 7 giorni (comprese
# quelle in ritardo) sulle ore di lavoro di una settimana.
ORE_SETTIMANALI = getattr(settings, 'TEAM_ORE_SETTIMANALI', 40)
CARICO_ALTO = 85
TINTE = 6


def _membri(utenti):
    """Gli utenti con i numeri delle card. `utenti` è un queryset di User."""
    ora = timezone.now()
    # I join di aggregazione non invocano il manager delle attività.
    attivita_azienda = Q(attivita_assegnate__azienda_id=current_azienda_id())
    aperte = attivita_azienda & Q(attivita_assegnate__completata=False)
    membri = list(
        utenti.filter(profile__isnull=False).select_related('profile').annotate(
            n_aperte=Count('attivita_assegnate', filter=aperte),
            n_completate=Count('attivita_assegnate', filter=attivita_azienda & Q(attivita_assegnate__completata=True)),
            n_ritardo=Count('attivita_assegnate',
                            filter=aperte & Q(attivita_assegnate__data__lt=ora)),
            minuti_7gg=Sum('attivita_assegnate__durata_minuti',
                           filter=aperte & Q(attivita_assegnate__data__lt=ora + dt.timedelta(days=7))),
        )
    )
    ids = [u.pk for u in membri]
    clienti = dict(
        Cliente.objects.filter(account_manager__in=ids).order_by()
        .values_list('account_manager').annotate(n=Count('pk'))
    )
    opportunita = {
        r['owner']: r for r in
        Opportunita.objects.filter(owner__in=ids)
        .exclude(stadio__in=[Stadio.CHIUSA_WIN, Stadio.CHIUSA_LOST]).order_by()
        .values('owner').annotate(n=Count('pk'), valore=Sum('valore'))
    }
    for u in membri:
        p = u.profile
        u.nome = u.get_full_name() or u.username
        u.sottotitolo = p.qualifica or u.email or p.ruolo_label
        # il tag della card è anche la chiave dei filtri
        u.gruppo = p.reparto or p.ruolo_label
        u.tinta = u.pk % TINTE
        u.ore_7gg = round((u.minuti_7gg or 0) / 60, 1)
        u.carico = round(u.ore_7gg / ORE_SETTIMANALI * 100)
        u.carico_barra = min(u.carico, 100)
        u.livello_carico = 'oltre' if u.carico > 100 else 'alto' if u.carico >= CARICO_ALTO else ''
        u.n_clienti = clienti.get(u.pk, 0)
        opp = opportunita.get(u.pk) or {}
        u.n_opportunita = opp.get('n', 0)
        u.pipeline = opp.get('valore') or 0
    return membri


def _reparti():
    return sorted(
        set(Profile.objects.exclude(reparto='').values_list('reparto', flat=True)),
        key=str.casefold,
    )


@login_required
def index(request):
    membri = _membri(
        utenti_azienda().filter(is_active=True).order_by('first_name', 'last_name', 'username')
    )
    gruppi = sorted({m.gruppo for m in membri}, key=str.casefold)
    apri = request.GET.get('membro', '')
    return render(request, 'team/index.html', {
        'active_nav': 'team',
        'membri': membri,
        'gruppi': gruppi if len(gruppi) > 1 else [],
        'apri_membro': int(apri) if apri.isdigit() else None,
        'puo_invitare': has_perm(request.user, 'utenti.gestisci'),
        'ruoli_choices': Ruolo.choices,
        'reparti': _reparti(),
        'ore_settimanali': ORE_SETTIMANALI,
    })


@login_required
def scheda(request, pk):
    """Il contenuto del pannello laterale; senza HTMX la pagina Team lo apre."""
    if not request.htmx:
        return redirect(f"{reverse('team:index')}?membro={pk}")
    membri = _membri(utenti_azienda().filter(pk=pk, is_active=True))
    if not membri:
        raise Http404
    m = membri[0]
    attivita = Attivita.objects.filter(owner=m).select_related('cliente')
    return render(request, 'team/_scheda.html', {
        'm': m,
        'da_fare': attivita.filter(completata=False).order_by('data')[:6],
        'fatte': attivita.filter(completata=True).order_by('-data')[:3],
        'ora': timezone.now(),
        'ore_settimanali': ORE_SETTIMANALI,
        'puo_modificare': request.user.pk == m.pk or has_perm(request.user, 'utenti.gestisci'),
        'puo_assegnare': has_perm(request.user, 'attivita.modifica'),
        'form': SchedaForm(instance=m.profile),
        'reparti': _reparti(),
    })


@login_required
@require_POST
def modifica(request, pk):
    utente = get_object_or_404(utenti_azienda().select_related('profile'), pk=pk, is_active=True)
    if request.user.pk != utente.pk and not has_perm(request.user, 'utenti.gestisci'):
        messages.error(request, 'Puoi modificare solo la tua scheda.')
        return redirect('team:index')
    form = SchedaForm(request.POST, instance=utente.profile)
    if form.is_valid():
        form.save()
        messages.success(request, 'Scheda aggiornata')
    else:
        messages.error(request, 'Scheda non salvata: controlla i campi.')
    return redirect(f"{reverse('team:index')}?membro={pk}")
