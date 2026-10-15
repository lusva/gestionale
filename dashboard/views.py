from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from accounts.permissions import has_perm

from .da_fare import voci_da_fare


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/dashboard.html'

    def get_context_data(self, **kwargs):
        from attivita.models import Attivita
        from clienti.models import Cliente, StatoCliente
        from opportunita.models import Opportunita, Stadio

        ctx = super().get_context_data(**kwargs)

        now = timezone.localtime()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start_of_prev_month = (start_of_month - timedelta(days=1)).replace(day=1)

        clienti = Cliente.objects.aggregate(
            attivi=Count('pk', filter=Q(stato=StatoCliente.ATTIVO)),
            nuovi=Count('pk', filter=Q(created_at__gte=start_of_month)),
        )
        chiuse = [Stadio.CHIUSA_WIN, Stadio.CHIUSA_LOST]
        opp = Opportunita.objects.aggregate(
            aperte=Count('pk', filter=~Q(stadio__in=chiuse)),
            vinte=Count('pk', filter=Q(stadio=Stadio.CHIUSA_WIN)),
            perse=Count('pk', filter=Q(stadio=Stadio.CHIUSA_LOST)),
            mtd=Sum('valore', filter=Q(stadio=Stadio.CHIUSA_WIN,
                                       data_chiusura__gte=start_of_month.date())),
            prev=Sum('valore', filter=Q(stadio=Stadio.CHIUSA_WIN,
                                        data_chiusura__gte=start_of_prev_month.date(),
                                        data_chiusura__lt=start_of_month.date())),
        )
        fatturato_mtd = opp['mtd'] or Decimal('0')
        fatturato_prev = opp['prev'] or Decimal('0')

        if fatturato_prev:
            delta_pct = int(round((fatturato_mtd - fatturato_prev) / fatturato_prev * 100))
        else:
            delta_pct = None

        vinte, perse = opp['vinte'], opp['perse']
        total_chiuse = vinte + perse
        tasso_chiusura = int(round((vinte / total_chiuse) * 100)) if total_chiuse else 0

        # Bar chart: fatturato per mese dell'anno corrente; sparkline: ultimi
        # 10 mesi. Tutti i mesi in poche query raggruppate per mese.
        year = now.year
        ultimi_10 = _last_10_months(now.date())
        dal = min(date(year, 1, 1), ultimi_10[0][2])
        fatturato = _fatturato_per_mese(dal, now.date()) if has_perm(self.request.user, 'documenti.vedi') else {}
        vendite = _per_mese(Opportunita.objects.filter(
            stadio=Stadio.CHIUSA_WIN, data_chiusura__range=(dal, now.date())),
            'data_chiusura', Sum('valore'))
        monthly = [fatturato.get((year, m), 0.0) for m in range(1, 13)]
        month_labels = ['Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu', 'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic']

        fatturato_anno = sum(monthly)

        # Riepilogo delle sole trattative aperte, in una query. Le card e il
        # riordino sono nella pipeline completa: qui nessun elenco troncato.
        pipeline_cols = []
        col_labels = {
            Stadio.NUOVA: ('Nuova', 'var(--ink-3)'),
            Stadio.QUALIFICATA: ('Qualificata', 'var(--info)'),
            Stadio.PROPOSTA: ('Proposta', 'var(--warn)'),
            Stadio.NEGOZIAZIONE: ('Negoziazione', 'var(--brand-violet)'),
        }
        per_stadio = {r['stadio']: r for r in Opportunita.objects.filter(
            stadio__in=col_labels).values('stadio').annotate(
            n=Count('pk'), tot=Sum('valore')).order_by()}
        for stadio in col_labels:
            label, color = col_labels[stadio]
            riga = per_stadio.get(stadio, {})
            pipeline_cols.append({
                'id': stadio,
                'label': label,
                'color': color,
                'count': riga.get('n', 0),
                'total': riga.get('tot') or Decimal('0'),
            })

        pipeline_total = sum(c['total'] for c in pipeline_cols)
        pipeline_count = sum(c['count'] for c in pipeline_cols)

        # Le prossime azioni personali, con gli arretrati in testa.
        agenda = Attivita.objects.filter(owner=self.request.user, completata=False)
        agenda_counts = agenda.aggregate(
            scadute=Count('pk', filter=Q(data__lt=now)),
            oggi=Count('pk', filter=Q(data__date=now.date())),
        )
        prossime_attivita = agenda.select_related('cliente', 'opportunita').order_by('data', 'pk')[:6]

        # KPI documenti (ciclo attivo + AR/AP), visibili con il relativo permesso.
        doc_kpi = None
        if has_perm(self.request.user, 'documenti.vedi'):
            from documenti.models import (
                ScadenzaFattura, ScadenzaFatturaAcquisto, TestataFattura, crediti_aperti,
                scadenze_acquisto_con_residuo,
            )
            today = now.date()
            fatture = TestataFattura.objects.aggregate(
                mese=Sum('imponibile', filter=Q(data_documento__gte=start_of_month.date(),
                                                data_documento__lte=today)),
                da_pagare=Count('pk', filter=Q(pagata=False)),
            )
            ar_scaduto = crediti_aperti(ScadenzaFattura.objects.filter(
                data__lt=today,
            )).aggregate(s=Sum('residuo_db'))['s'] or Decimal('0')
            ap_scaduto = scadenze_acquisto_con_residuo(
                ScadenzaFatturaAcquisto.objects.filter(data_scadenza__lt=today),
            ).filter(residuo_db__gt=0).aggregate(s=Sum('residuo_db'))['s'] or Decimal('0')
            centesimi = Decimal('0.01')
            doc_kpi = {
                'fatt_mese': fatture['mese'] or Decimal('0'),
                'ar_scaduto': Decimal(ar_scaduto).quantize(centesimi),
                'ap_scaduto': Decimal(ap_scaduto).quantize(centesimi),
                'fatture_da_pagare': fatture['da_pagare'],
            }

        ctx.update({
            'user_name': self.request.user.get_short_name() or self.request.user.username,
            'today': now,
            'kpi_clienti_attivi': clienti['attivi'],
            'kpi_clienti_new_month': clienti['nuovi'],
            'kpi_opp_aperte': opp['aperte'],
            'kpi_fatturato_mtd': fatturato_mtd,
            'kpi_fatturato_delta_pct': delta_pct,
            'kpi_tasso_chiusura': tasso_chiusura,
            'kpi_opportunita_chiuse': total_chiuse,
            'kpi_opportunita_vinte': vinte,
            'bar_values': monthly,
            'bar_labels': month_labels,
            'fatturato_anno': fatturato_anno,
            'pipeline_cols': pipeline_cols,
            'pipeline_total': pipeline_total,
            'pipeline_count': pipeline_count,
            'prossime_attivita': prossime_attivita,
            'agenda_counts': agenda_counts,
            'active_nav': 'dashboard',
            'spark_clienti': _spark_creati(Cliente, ultimi_10),
            'spark_opp': _spark_creati(Opportunita, ultimi_10),
            'spark_vendite': [float(vendite.get((y, m)) or 0) for y, m, _, _ in ultimi_10],
            'spark_chiusura': _spark_tasso_chiusura(ultimi_10),
            'doc_kpi': doc_kpi,
            'da_fare': voci_da_fare(self.request.user),
        })
        return ctx


def _last_10_months(today=None):
    """Ritorna lista di ``(year, month, first_day, last_day)`` per gli ultimi 10 mesi."""
    from calendar import monthrange

    today = today or timezone.localdate()
    out = []
    y, m = today.year, today.month
    for _ in range(10):
        last_day = monthrange(y, m)[1]
        out.append((y, m, date(y, m, 1), date(y, m, last_day)))
        # mese precedente
        if m == 1:
            y -= 1
            m = 12
        else:
            m -= 1
    return list(reversed(out))


def _per_mese(qs, campo, valore) -> dict:
    """``{(anno, mese): valore}`` raggruppando ``qs`` per mese di ``campo``
    (una query)."""
    righe = (qs.annotate(mese=TruncMonth(campo)).values('mese')
             .annotate(v=valore).order_by())
    return {(r['mese'].year, r['mese'].month): r['v'] for r in righe if r['mese']}


def _spark_creati(model, mesi):
    """Oggetti creati per ciascuno dei ``mesi`` (``_last_10_months``)."""
    conteggi = _per_mese(model.objects.filter(created_at__date__gte=mesi[0][2]),
                         'created_at', Count('pk'))
    return [conteggi.get((y, m), 0) for y, m, _, _ in mesi]


def _fatturato_per_mese(dal, al) -> dict:
    """Fatturato per mese tra ``dal`` e ``al`` (inclusi): ``{(anno, mese): float}``.

    Solo imponibile fatture: una trattativa vinta non è una fattura e
    sommare i due importi conterebbe due volte la stessa vendita.
    """
    from documenti.models import TestataFattura
    fatt = _per_mese(TestataFattura.objects.filter(data_documento__range=(dal, al)),
                     'data_documento', Sum('imponibile'))
    return {k: float(v or Decimal('0')) for k, v in fatt.items()}


def _spark_tasso_chiusura(mesi):
    """Tasso di chiusura per mese (% vinte / (vinte + perse))."""
    from opportunita.models import Opportunita, Stadio
    chiuse = Opportunita.objects.filter(data_chiusura__range=(mesi[0][2], mesi[-1][3]))
    vinte = _per_mese(chiuse.filter(stadio=Stadio.CHIUSA_WIN), 'data_chiusura', Count('pk'))
    perse = _per_mese(chiuse.filter(stadio=Stadio.CHIUSA_LOST), 'data_chiusura', Count('pk'))
    points = []
    for y, m, _, _ in mesi:
        v, p = vinte.get((y, m), 0), perse.get((y, m), 0)
        points.append(int(round(v / (v + p) * 100)) if v + p else 0)
    return points


@require_POST
def set_theme(request):
    theme = request.POST.get('theme', 'light')
    if theme not in {'light', 'dark'}:
        theme = 'light'
    if request.user.is_authenticated:
        profile = getattr(request.user, 'profile', None)
        if profile:
            profile.tema = theme
            profile.save(update_fields=['tema'])
    resp = HttpResponse(status=204)
    resp.set_cookie('gest_theme', theme, max_age=60 * 60 * 24 * 365, samesite='Lax')
    return resp


@require_POST
def set_density(request):
    density = request.POST.get('density', 'normal')
    if density not in {'compact', 'normal', 'comfy'}:
        density = 'normal'
    if request.user.is_authenticated:
        profile = getattr(request.user, 'profile', None)
        if profile:
            profile.densita = density
            profile.save(update_fields=['densita'])
    resp = HttpResponse(status=204)
    resp.set_cookie('gest_density', density, max_age=60 * 60 * 24 * 365, samesite='Lax')
    return resp
