from __future__ import annotations

import io
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.generic import TemplateView

from accounts.permissions import require_perm
from audit.models import Azione
from audit.utils import log as audit_log


BRAND_PALETTE = [
    'var(--brand-purple)',
    'var(--brand-violet)',
    'var(--brand-sky)',
    'var(--brand-teal)',
    'var(--ink-4)',
    'var(--warn)',
    'var(--info)',
]


class ReportView(LoginRequiredMixin, TemplateView):
    template_name = 'reports/index.html'

    def get_context_data(self, **kwargs):
        from clienti.models import Cliente
        from opportunita.models import Opportunita, Stadio

        ctx = super().get_context_data(**kwargs)
        now = timezone.now()

        periodo = self.request.GET.get('periodo', '30g')
        if periodo == '30g':
            dal = now - timedelta(days=30)
            label = 'Ultimi 30 giorni'
        elif periodo == 'q2':
            dal = now.replace(month=4, day=1, hour=0, minute=0, second=0, microsecond=0)
            label = 'Q2 2026'
        elif periodo == 'anno':
            dal = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            label = 'Anno 2026'
        else:
            dal = now - timedelta(days=30)
            label = 'Periodo personalizzato'

        chiuse_win = Opportunita.objects.filter(stadio=Stadio.CHIUSA_WIN, updated_at__gte=dal)
        ricavi = chiuse_win.aggregate(s=Sum('valore'))['s'] or Decimal('0')
        nuovi_clienti = Cliente.objects.filter(created_at__gte=dal).count()
        vinte_count = chiuse_win.count()
        valore_medio = (ricavi / vinte_count) if vinte_count else Decimal('0')

        perse = Opportunita.objects.filter(stadio=Stadio.CHIUSA_LOST, updated_at__gte=dal).count()
        total_chiuse = vinte_count + perse
        churn = round((perse / total_chiuse) * 100, 1) if total_chiuse else 0.0

        # Bar chart: fatturato per mese 2026
        year = now.year
        monthly = []
        for m in range(1, 13):
            s = Opportunita.objects.filter(
                stadio=Stadio.CHIUSA_WIN, updated_at__year=year, updated_at__month=m,
            ).aggregate(s=Sum('valore'))['s'] or 0
            monthly.append(float(s))
        max_m = max(monthly) if monthly else 0
        bars = [round(v / max_m * 95, 1) if max_m else 0 for v in monthly]
        month_labels = ['Gen', 'Feb', 'Mar', 'Apr', 'Mag', 'Giu', 'Lug', 'Ago', 'Set', 'Ott', 'Nov', 'Dic']

        # Donut: fatturato per settore
        by_sector = (
            Opportunita.objects.filter(stadio=Stadio.CHIUSA_WIN)
            .values('cliente__settore__nome')
            .annotate(total=Sum('valore'))
            .order_by('-total')
        )
        sectors = []
        totalv = sum(r['total'] or 0 for r in by_sector) or 1
        acc = Decimal('0')
        for i, row in enumerate(by_sector[:5]):
            label_s = row['cliente__settore__nome'] or 'Altri'
            val = Decimal(row['total'] or 0)
            pct = round(float(val / totalv * 100), 1)
            sectors.append({
                'label': label_s,
                'val': float(val),
                'pct': pct,
                'color': BRAND_PALETTE[i % len(BRAND_PALETTE)],
            })
        # build arc path data
        start = 0.0
        donut_arcs = []
        for s in sectors:
            end = start + s['pct']
            donut_arcs.append({
                **s,
                'path': _arc_path(start, end),
            })
            start = end

        # Top 5 clienti per fatturato
        top5 = (
            Cliente.objects
            .annotate(fatturato=Sum('opportunita__valore', filter=None))
            .order_by('-fatturato')[:5]
        )
        top5_rows = []
        tot_top = Opportunita.objects.filter(stadio=Stadio.CHIUSA_WIN).aggregate(s=Sum('valore'))['s'] or Decimal('1')
        for rank, c in enumerate(top5, start=1):
            f = c.fatturato or 0
            top5_rows.append({
                'rank': rank,
                'cliente': c,
                'fatturato': f,
                'pct': round(float(f / tot_top * 100), 1) if tot_top else 0.0,
                'up': True,
            })

        ctx.update({
            'active_nav': 'report',
            'today': now,
            'periodo': periodo,
            'periodo_label': label,
            'kpi_ricavi': ricavi,
            'kpi_nuovi_clienti': nuovi_clienti,
            'kpi_valore_medio': valore_medio,
            'kpi_churn': churn,
            'bar_data': bars,
            'bar_labels': month_labels,
            'sectors': sectors,
            'donut_arcs': donut_arcs,
            'top5': top5_rows,
        })
        return ctx


def _arc_path(start_pct, end_pct, cx=80, cy=80, r=60):
    """Build an SVG pie slice path. start/end expressed as 0-100."""
    import math
    a1 = (start_pct / 100) * math.pi * 2 - math.pi / 2
    a2 = (end_pct / 100) * math.pi * 2 - math.pi / 2
    x1 = cx + r * math.cos(a1)
    y1 = cy + r * math.sin(a1)
    x2 = cx + r * math.cos(a2)
    y2 = cy + r * math.sin(a2)
    large = 1 if end_pct - start_pct > 50 else 0
    return f'M {cx} {cy} L {x1:.2f} {y1:.2f} A {r} {r} 0 {large} 1 {x2:.2f} {y2:.2f} Z'


@require_perm('report.esporta')
def pdf(request):
    """Esporta il report corrente in PDF (xhtml2pdf/pisa).

    Riutilizza lo stesso calcolo di `ReportView` e rende
    `reports/pdf.html` (template PDF-friendly).
    """
    from xhtml2pdf import pisa

    view = ReportView()
    view.request = request
    view.kwargs = {}
    ctx = view.get_context_data()

    html = render_to_string('reports/pdf.html', ctx)
    buffer = io.BytesIO()
    result = pisa.CreatePDF(src=html, dest=buffer, encoding='utf-8')
    if result.err:
        return HttpResponse('Errore generazione PDF', status=500)

    periodo_slug = ctx.get('periodo', 'report')
    today = timezone.now().strftime('%Y%m%d')
    filename = f'report-{periodo_slug}-{today}.pdf'

    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    audit_log(
        Azione.EXPORT, target_type='Report',
        target_label=filename, request=request,
        meta={'periodo': ctx.get('periodo')},
    )
    return response
