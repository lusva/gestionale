from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from django.views.generic import TemplateView


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/dashboard.html'

    def get_context_data(self, **kwargs):
        from attivita.models import Attivita
        from clienti.models import Cliente, StatoCliente
        from opportunita.models import Opportunita, PIPELINE_COLUMNS, Stadio

        ctx = super().get_context_data(**kwargs)

        now = timezone.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start_of_prev_month = (start_of_month - timedelta(days=1)).replace(day=1)

        clienti_attivi = Cliente.objects.filter(stato=StatoCliente.ATTIVO).count()
        clienti_new_month = Cliente.objects.filter(created_at__gte=start_of_month).count()

        opp_aperte = Opportunita.objects.exclude(
            stadio__in=[Stadio.CHIUSA_WIN, Stadio.CHIUSA_LOST],
        )
        opp_aperte_count = opp_aperte.count()

        fatturato_mtd = Opportunita.objects.filter(
            stadio=Stadio.CHIUSA_WIN, updated_at__gte=start_of_month,
        ).aggregate(s=Sum('valore'))['s'] or Decimal('0')

        fatturato_prev = Opportunita.objects.filter(
            stadio=Stadio.CHIUSA_WIN,
            updated_at__gte=start_of_prev_month,
            updated_at__lt=start_of_month,
        ).aggregate(s=Sum('valore'))['s'] or Decimal('0')

        if fatturato_prev:
            delta_pct = int(round((fatturato_mtd - fatturato_prev) / fatturato_prev * 100))
        else:
            delta_pct = 0 if fatturato_mtd == 0 else 100

        vinte = Opportunita.objects.filter(stadio=Stadio.CHIUSA_WIN).count()
        perse = Opportunita.objects.filter(stadio=Stadio.CHIUSA_LOST).count()
        total_chiuse = vinte + perse
        tasso_chiusura = int(round((vinte / total_chiuse) * 100)) if total_chiuse else 0

        # Bar chart: fatturato per month of current year
        year = now.year
        monthly = []
        for m in range(1, 13):
            s = Opportunita.objects.filter(
                stadio=Stadio.CHIUSA_WIN,
                updated_at__year=year,
                updated_at__month=m,
            ).aggregate(s=Sum('valore'))['s'] or 0
            monthly.append(float(s))
        month_labels = ['Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu', 'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic']
        max_month = max(monthly) if monthly else 0
        if max_month == 0:
            bars = [0] * 12
        else:
            bars = [round(v / max_month * 95, 1) for v in monthly]

        fatturato_anno = sum(monthly)

        # Pipeline columns
        pipeline_cols = []
        col_labels = {
            Stadio.NUOVA: ('Nuova', 'var(--ink-3)'),
            Stadio.QUALIFICATA: ('Qualificata', 'var(--info)'),
            Stadio.PROPOSTA: ('Proposta', 'var(--warn)'),
            Stadio.NEGOZIAZIONE: ('Negoziazione', 'var(--brand-violet)'),
            Stadio.CHIUSA_WIN: ('Chiusa', 'var(--success)'),
        }
        for stadio in PIPELINE_COLUMNS:
            opps = Opportunita.objects.filter(stadio=stadio).select_related('cliente', 'owner__profile')
            label, color = col_labels[stadio]
            total_val = sum(o.valore for o in opps)
            pipeline_cols.append({
                'id': stadio,
                'label': label,
                'color': color,
                'items': list(opps)[:8],
                'count': opps.count(),
                'total': total_val,
            })

        pipeline_total = sum(c['total'] for c in pipeline_cols)
        pipeline_count = sum(c['count'] for c in pipeline_cols)

        # Recent activity
        recent_activity = Attivita.objects.select_related(
            'cliente', 'owner__profile',
        ).order_by('-created_at')[:6]

        # KPI documenti (ciclo attivo + AR/AP) — solo se la app è installata
        doc_kpi = None
        try:
            from documenti.models import (
                ScadenzaFattura, ScadenzaFatturaAcquisto, TestataFattura,
            )
            today = now.date()
            fatt_doc_mese = TestataFattura.objects.filter(
                data_documento__gte=start_of_month.date(),
                data_documento__lte=today,
            ).aggregate(s=Sum('imponibile'))['s'] or Decimal('0')
            ar_scaduto = Decimal('0')
            for s in ScadenzaFattura.objects.select_related('fattura').filter(
                fattura__pagata=False, data__lt=today,
            ):
                ar_scaduto += s.importo
            ap_scaduto = Decimal('0')
            for s in ScadenzaFatturaAcquisto.objects.filter(data_scadenza__lt=today):
                if s.importo_residuo > 0:
                    ap_scaduto += s.importo_residuo
            fatture_da_pagare = TestataFattura.objects.filter(pagata=False).count()
            doc_kpi = {
                'fatt_mese': fatt_doc_mese,
                'ar_scaduto': ar_scaduto,
                'ap_scaduto': ap_scaduto,
                'fatture_da_pagare': fatture_da_pagare,
            }
        except Exception:
            doc_kpi = None

        ctx.update({
            'user_name': self.request.user.get_short_name() or self.request.user.username,
            'today': now,
            'kpi_clienti_attivi': clienti_attivi,
            'kpi_clienti_new_month': clienti_new_month,
            'kpi_opp_aperte': opp_aperte_count,
            'kpi_fatturato_mtd': fatturato_mtd,
            'kpi_fatturato_delta_pct': delta_pct,
            'kpi_tasso_chiusura': tasso_chiusura,
            'bar_data': bars,
            'bar_labels': month_labels,
            'fatturato_anno': fatturato_anno,
            'pipeline_cols': pipeline_cols,
            'pipeline_total': pipeline_total,
            'pipeline_count': pipeline_count,
            'recent_activity': recent_activity,
            'active_nav': 'dashboard',
            'spark_clienti': _spark_clienti(),
            'spark_opp': _spark_opp(),
            'spark_fatturato': _spark_fatturato(),
            'spark_chiusura': _spark_tasso_chiusura(),
            'doc_kpi': doc_kpi,
        })
        return ctx


def _last_10_months(today=None):
    """Ritorna lista di ``(year, month, first_day, last_day)`` per gli ultimi 10 mesi."""
    from calendar import monthrange
    from datetime import date as _date

    today = today or timezone.now().date()
    out = []
    y, m = today.year, today.month
    for _ in range(10):
        last_day = monthrange(y, m)[1]
        out.append((y, m, _date(y, m, 1), _date(y, m, last_day)))
        # mese precedente
        if m == 1:
            y -= 1
            m = 12
        else:
            m -= 1
    return list(reversed(out))


def _spark_clienti():
    """Clienti creati per ciascuno degli ultimi 10 mesi (pre-attivi inclusi)."""
    from clienti.models import Cliente
    points = []
    for y, m, first, last in _last_10_months():
        n = Cliente.objects.filter(
            created_at__year=y, created_at__month=m,
        ).count()
        points.append(n)
    return points


def _spark_opp():
    """Opportunità create per mese (ultimi 10 mesi)."""
    from opportunita.models import Opportunita
    points = []
    for y, m, first, last in _last_10_months():
        n = Opportunita.objects.filter(
            created_at__year=y, created_at__month=m,
        ).count()
        points.append(n)
    return points


def _spark_fatturato():
    """Fatturato per mese (ultimi 10).

    Combina (a) opportunità chiuse-WIN nel mese e (b) imponibile fatture
    con ``data_documento`` nel mese — somma per dare un quadro unico.
    """
    from decimal import Decimal as _D
    from opportunita.models import Opportunita, Stadio
    points = []
    try:
        from documenti.models import TestataFattura
    except Exception:
        TestataFattura = None
    for y, m, first, last in _last_10_months():
        opp = Opportunita.objects.filter(
            stadio=Stadio.CHIUSA_WIN,
            updated_at__year=y, updated_at__month=m,
        ).aggregate(s=Sum('valore'))['s'] or _D('0')
        fatt = _D('0')
        if TestataFattura is not None:
            fatt = TestataFattura.objects.filter(
                data_documento__gte=first, data_documento__lte=last,
            ).aggregate(s=Sum('imponibile'))['s'] or _D('0')
        points.append(float(opp + fatt))
    return points


def _spark_tasso_chiusura():
    """Tasso di chiusura per mese (% vinte / (vinte + perse))."""
    from opportunita.models import Opportunita, Stadio
    points = []
    for y, m, first, last in _last_10_months():
        v = Opportunita.objects.filter(
            stadio=Stadio.CHIUSA_WIN,
            updated_at__year=y, updated_at__month=m,
        ).count()
        p = Opportunita.objects.filter(
            stadio=Stadio.CHIUSA_LOST,
            updated_at__year=y, updated_at__month=m,
        ).count()
        total = v + p
        points.append(int(round(v / total * 100)) if total else 0)
    return points


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
