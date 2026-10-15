"""«Da fare»: quello che aspetta l'utente, calcolato al momento.

Crediti e debiti scaduti o in scadenza, scadenze fiscali, fatture scartate
dal SDI o ancora da inviare, fatture di acquisto da confermare, attività in
ritardo. Lo mostrano la campanella della topbar (caricata dopo la pagina,
via HTMX) e la dashboard. Solo aggregati: poche query, nessuna per riga.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Min, Q, Sum
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from accounts.permissions import has_perm

URGENTE, ATTENZIONE, INFO = 'urgente', 'attenzione', 'info'


@dataclass
class Voce:
    livello: str
    icona: str
    titolo: str
    dettaglio: str
    url: str
    conteggio: int = 1


def _euro(v) -> str:
    from core_settings.templatetags.ui import euro
    return euro(v or Decimal('0'), 2)


def voci_da_fare(user) -> list[Voce]:
    oggi = timezone.localdate()
    fra_7 = oggi + timedelta(days=7)
    voci: list[Voce] = []
    if not user.is_authenticated:
        return voci

    if has_perm(user, 'documenti.vedi'):
        from anagrafiche.models import AnagraficaAzienda
        from documenti.models import (
            ScadenzaFattura, ScadenzaFatturaAcquisto, TestataFattura, TestataFatturaAcquisto,
            TestataNotaCredito, crediti_aperti, scadenze_acquisto_con_residuo,
        )

        # sul residuo: un incasso parziale riduce il credito
        crediti = crediti_aperti(ScadenzaFattura.objects.filter(data__lte=fra_7)).aggregate(
            n_scaduti=Count('fattura', distinct=True, filter=Q(data__lt=oggi)),
            scaduti=Sum('residuo_db', filter=Q(data__lt=oggi)),
            dal=Min('data', filter=Q(data__lt=oggi)),
            n_vicini=Count('fattura', distinct=True, filter=Q(data__gte=oggi, data__lte=fra_7)),
            vicini=Sum('residuo_db', filter=Q(data__gte=oggi, data__lte=fra_7)),
        )
        scadenziario = reverse('documenti:scadenziario_attivo')
        if crediti['n_scaduti']:
            n = crediti['n_scaduti']
            voci.append(Voce(
                URGENTE, 'euro',
                f'{n} fattur{"a" if n == 1 else "e"} scadut{"a" if n == 1 else "e"} da incassare',
                f'{_euro(crediti["scaduti"])} · la più vecchia dal {crediti["dal"]:%d/%m/%Y}',
                scadenziario, n))
        if crediti['n_vicini']:
            n = crediti['n_vicini']
            voci.append(Voce(
                INFO, 'clock', f'{n} incass{"o" if n == 1 else "i"} attes{"o" if n == 1 else "i"} entro 7 giorni',
                _euro(crediti['vicini']), scadenziario, n))

        debiti = scadenze_acquisto_con_residuo(
            ScadenzaFatturaAcquisto.objects.filter(data_scadenza__lte=fra_7),
        ).filter(residuo_db__gt=0).aggregate(
            n_scaduti=Count('pk', filter=Q(data_scadenza__lt=oggi)),
            scaduti=Sum('residuo_db', filter=Q(data_scadenza__lt=oggi)),
            n_vicini=Count('pk', filter=Q(data_scadenza__gte=oggi)),
            vicini=Sum('residuo_db', filter=Q(data_scadenza__gte=oggi)),
        )
        passivo = reverse('documenti:scadenziario_passivo')
        if debiti['n_scaduti']:
            n = debiti['n_scaduti']
            voci.append(Voce(URGENTE, 'alert',
                             f'{n} pagament{"o" if n == 1 else "i"} a fornitori scadut{"o" if n == 1 else "i"}',
                             _euro(debiti['scaduti']), passivo, n))
        if debiti['n_vicini']:
            n = debiti['n_vicini']
            voci.append(Voce(ATTENZIONE, 'clock',
                             f'{n} pagament{"o" if n == 1 else "i"} a fornitori entro 7 giorni',
                             _euro(debiti['vicini']), passivo, n))

        lista = reverse('documenti:fattura_list')
        fatture = TestataFattura.objects.aggregate(
            scartate=Count('pk', filter=Q(sdi_stato__in=TestataFattura.SDI_STATI_REINVIO)),
            da_inviare=Count('pk', filter=Q(sdi_stato=TestataFattura.SdiStato.NON_INVIATA)),
            mancata=Count('pk', filter=Q(sdi_stato=TestataFattura.SdiStato.MANCATA_CONSEGNA)),
        )
        note = TestataNotaCredito.objects.aggregate(
            scartate=Count('pk', filter=Q(sdi_stato__in=TestataNotaCredito.SDI_STATI_REINVIO)),
            da_inviare=Count('pk', filter=Q(sdi_stato=TestataNotaCredito.SdiStato.NON_INVIATA)),
        )
        url_note = reverse('documenti:nota_credito_list')
        if note['scartate']:
            n = note['scartate']
            voci.append(Voce(URGENTE, 'x',
                             f'{n} not{"a" if n == 1 else "e"} di credito scartat{"a" if n == 1 else "e"} o rifiutat{"a" if n == 1 else "e"}',
                             'Correggi e reinvia al SDI', f'{url_note}?sdi=scartata', n))
        if fatture['scartate']:
            n = fatture['scartate']
            voci.append(Voce(URGENTE, 'x',
                             f'{n} fattur{"a scartata" if n == 1 else "e scartate"} o rifiutat{"a" if n == 1 else "e"}',
                             'Correggi e reinvia al SDI entro 5 giorni dallo scarto',
                             f'{lista}?sdi=scartata', n))
        if fatture['mancata']:
            n = fatture['mancata']
            voci.append(Voce(INFO, 'mail', f'{n} fattur{"a" if n == 1 else "e"} in mancata consegna',
                             'Il SDI l\'ha messa nell\'area riservata del cliente: avvisalo',
                             f'{lista}?sdi=mancata_consegna', n))
        az = AnagraficaAzienda.objects.only('sdi_provider').first()
        if (fatture['da_inviare'] and az is not None
                and az.sdi_provider != AnagraficaAzienda.SdiProvider.DISABILITATO):
            n = fatture['da_inviare']
            voci.append(Voce(ATTENZIONE, 'upload',
                             f'{n} fattur{"a" if n == 1 else "e"} da inviare al SDI',
                             'Vanno trasmesse entro 12 giorni dalla data del documento',
                             f'{lista}?sdi=non_inviata', n))
        if (note['da_inviare'] and az is not None
                and az.sdi_provider != AnagraficaAzienda.SdiProvider.DISABILITATO):
            n = note['da_inviare']
            voci.append(Voce(ATTENZIONE, 'upload',
                             f'{n} not{"a" if n == 1 else "e"} di credito da inviare al SDI',
                             'Come le fatture, entro 12 giorni dalla data del documento',
                             f'{url_note}?sdi=non_inviata', n))

        bozze = TestataFatturaAcquisto.objects.filter(
            stato=TestataFatturaAcquisto.StatoFattura.BOZZA).count()
        if bozze:
            voci.append(Voce(INFO, 'doc',
                             f'{bozze} fattur{"a" if bozze == 1 else "e"} di acquisto da confermare',
                             'Arrivate dal SDI: controllale e confermale',
                             reverse('documenti:fattura_acquisto_list') + '?stato=bozza', bozze))

        voci.extend(_voci_ricorrenti(oggi, sdi_attivo=bool(
            az is not None and az.sdi_provider != AnagraficaAzienda.SdiProvider.DISABILITATO)))

    if has_perm(user, 'cashflow.vedi'):
        from cashflow.models import ScadenzaFiscale, ScadenzaSpesa
        fra_10 = oggi + timedelta(days=10)
        fiscali = ScadenzaFiscale.objects.filter(pagata=False, data_scadenza__lte=fra_10).aggregate(
            n_scadute=Count('pk', filter=Q(data_scadenza__lt=oggi)),
            scadute=Sum('importo', filter=Q(data_scadenza__lt=oggi)),
            n_vicine=Count('pk', filter=Q(data_scadenza__gte=oggi)),
            vicine=Sum('importo', filter=Q(data_scadenza__gte=oggi)),
            prossima=Min('data_scadenza', filter=Q(data_scadenza__gte=oggi)),
        )
        url = reverse('cashflow:scadenza_fiscale_list')
        if fiscali['n_scadute']:
            n = fiscali['n_scadute']
            voci.append(Voce(URGENTE, 'briefcase',
                             f'{n} scadenz{"a" if n == 1 else "e"} fiscal{"e" if n == 1 else "i"} non pagat{"a" if n == 1 else "e"}',
                             f'{_euro(fiscali["scadute"])} · se le hai già versate segnale pagate',
                             f'{url}?stato=scadute', n))
        if fiscali['n_vicine']:
            n = fiscali['n_vicine']
            voci.append(Voce(ATTENZIONE, 'briefcase',
                             f'{n} scadenz{"a" if n == 1 else "e"} fiscal{"e" if n == 1 else "i"} entro 10 giorni',
                             f'{_euro(fiscali["vicine"])} · la prima il {fiscali["prossima"]:%d/%m}',
                             f'{url}?stato=aperte', n))
        spese = ScadenzaSpesa.objects.filter(pagata=False, data_scadenza__lt=oggi).aggregate(
            n=Count('pk'), tot=Sum('importo'))
        if spese['n']:
            n = spese['n']
            voci.append(Voce(ATTENZIONE, 'clock',
                             f'{n} spes{"a" if n == 1 else "e"} ricorrent{"e" if n == 1 else "i"} da segnare pagat{"a" if n == 1 else "e"}',
                             _euro(spese['tot']), reverse('cashflow:spesa_ricorrente_list'), n))

    from attivita.models import Attivita
    attivita = Attivita.objects.filter(completata=False, data__lt=timezone.now(), owner=user).count()
    if attivita:
        voci.append(Voce(ATTENZIONE, 'calendar',
                         f'{attivita} attività in ritardo', 'Assegnate a te e non completate',
                         reverse('attivita:list') + '?owner=me&stato=da_fare&periodo=scadute', attivita))

    from opportunita.models import Opportunita, Stadio
    from django.db.models import Exists, OuterRef

    followup = Attivita.objects.filter(opportunita_id=OuterRef('pk'), completata=False)
    trattative = Opportunita.objects.filter(owner=user).exclude(
        stadio__in=(Stadio.CHIUSA_WIN, Stadio.CHIUSA_LOST),
    ).annotate(ha_followup=Exists(followup)).aggregate(
        in_ritardo=Count('pk', filter=Q(chiusura_prevista__lt=oggi)),
        senza_followup=Count('pk', filter=Q(ha_followup=False)),
    )
    pipeline = reverse('opportunita:list') + '?owner=me&scope=open'
    if trattative['in_ritardo']:
        n = trattative['in_ritardo']
        voci.append(Voce(ATTENZIONE, 'target',
                         f'{n} opportunità oltre la chiusura prevista',
                         'Aggiorna la previsione o concorda il prossimo passo',
                         pipeline + '&attenzione=overdue', n))
    if trattative['senza_followup']:
        n = trattative['senza_followup']
        voci.append(Voce(INFO, 'calendar',
                         f'{n} opportunità senza attività pianificate',
                         'Aggiungi una chiamata, un incontro o un promemoria',
                         pipeline + '&attenzione=no_followup', n))

    ordine = {URGENTE: 0, ATTENZIONE: 1, INFO: 2}
    voci.sort(key=lambda v: ordine[v.livello])
    return voci


def _voci_ricorrenti(oggi, sdi_attivo: bool) -> list[Voce]:
    """Fatture ricorrenti: emissioni non riuscite, fatture emesse oggi da
    guardare, periodi rimasti indietro (il timer non gira?) e prossime
    emissioni. Tre query in tutto: le date si calcolano dai campi."""
    from documenti import ricorrenti
    from documenti.models import EmissioneRicorrente, FatturaRicorrente, TestataFattura

    voci = []
    url = reverse('documenti:ricorrente_list')
    E = EmissioneRicorrente.Esito
    emissioni = EmissioneRicorrente.objects.aggregate(
        errori=Count('pk', filter=Q(esito=E.ERRORE, ricorrenza__attiva=True)),
        oggi=Count('pk', filter=Q(esito=E.EMESSA, aggiornata_il__date=oggi)),
        da_inviare=Count('pk', filter=Q(esito=E.EMESSA, aggiornata_il__date=oggi,
                                        fattura__sdi_stato=TestataFattura.SdiStato.NON_INVIATA)),
    )
    if emissioni['errori']:
        n = emissioni['errori']
        voci.append(Voce(URGENTE, 'alert', f'{n} fattur{"a ricorrente non emessa" if n == 1 else "e ricorrenti non emesse"}',
                         'Guarda l\'errore nella ricorrenza, correggi e «Emetti ora»', url, n))
    if emissioni['oggi']:
        n = emissioni['oggi']
        dettaglio = ('Controllale e inviale al SDI' if sdi_attivo and emissioni['da_inviare']
                     else 'Controllale nell\'elenco delle fatture')
        voci.append(Voce(INFO, 'doc', f'{n} fattur{"a ricorrente emessa" if n == 1 else "e ricorrenti emesse"} oggi',
                         dettaglio, reverse('documenti:fattura_list') + f'?anno={oggi.year}', n))
    arretrate, prossime = 0, []
    for r in FatturaRicorrente.objects.filter(attiva=True).prefetch_related('emissioni'):
        arretrate += sum(1 for p in ricorrenti.da_emettere(r, oggi) if p.previsto < oggi)
        prossime += [p for p in ricorrenti.prossime(r, oggi, 1) if p.previsto <= oggi + timedelta(days=7)]
    if arretrate:
        voci.append(Voce(ATTENZIONE, 'clock', f'{arretrate} period{"o" if arretrate == 1 else "i"} ricorrent{"e" if arretrate == 1 else "i"} da emettere',
                         'Non emessi dal controllo di ogni mattina: «Emetti ora» dalla ricorrenza', url, arretrate))
    if prossime:
        n = len(prossime)
        voci.append(Voce(INFO, 'calendar', f'{n} fattur{"a ricorrente" if n == 1 else "e ricorrenti"} entro 7 giorni',
                         f'La prima il {min(p.previsto for p in prossime):%d/%m}', url, n))
    return voci


def conteggio(voci: list[Voce]) -> int:
    """Il numero sulla campanella: le cose urgenti o da guardare presto."""
    return sum(v.conteggio for v in voci if v.livello in (URGENTE, ATTENZIONE))


@login_required
def da_fare(request):
    voci = voci_da_fare(request.user)
    if request.GET.get('formato') == 'badge':
        n = conteggio(voci)
        return render(request, 'components/_da_fare_badge.html', {'n': n})
    return render(request, 'components/_da_fare.html', {'voci': voci})
