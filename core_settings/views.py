import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives
from django.core.paginator import Paginator
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from accounts.models import Profile, Ruolo, StatoUtente
from accounts.permissions import require_perm
from api.models import ApiToken, Webhook, WebhookEvento
from audit.models import AuditLog, Azione
from audit.utils import log as registra_audit

from .models import Organizzazione


logger = logging.getLogger(__name__)


def _send_invito_email(user, password, inviter, ruolo_label):
    """Spedisce la mail di benvenuto con credenziali temporanee.

    In DEBUG il backend è `console` (vedi settings), quindi l'email viene
    stampata in stdout di runserver.
    """
    org = Organizzazione.current()
    login_url = f'{settings.SITE_BASE_URL.rstrip("/")}{reverse("accounts:login")}'
    ctx = {
        'nome': (user.first_name or '').strip(),
        'email': user.email,
        'password': password,
        'login_url': login_url,
        'inviter_nome': (inviter.get_full_name() or inviter.username) if inviter else '',
        'org_nome': org.nome,
        'ruolo_label': ruolo_label,
    }
    subject = f'Invito a {org.nome} · Gestionale CRM'
    text_body = render_to_string('emails/invito_utente.txt', ctx)
    html_body = render_to_string('emails/invito_utente.html', ctx)
    try:
        msg = EmailMultiAlternatives(
            subject=subject, body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )
        msg.attach_alternative(html_body, 'text/html')
        msg.send(fail_silently=False)
    except Exception as exc:
        logger.exception('Errore invio email invito a %s: %s', user.email, exc)
        return False
    return True


SECTIONS = [
    ('organizzazione', 'Organizzazione'),
    ('utenti', 'Utenti e permessi'),
    ('ruoli', 'Ruoli'),
    ('integrazioni', 'Integrazioni'),
    ('fatturazione', 'Fatturazione'),
    ('documenti', 'Intestazione documenti'),
    ('api', 'API & Webhook'),
    ('audit', 'Audit log'),
    ('backup', 'Backup'),
]


PERMESSI_MATRICE = [
    ('Visualizzare clienti', (True, True, True)),
    ('Modificare clienti', (True, True, False)),
    ('Eliminare clienti', (True, False, False)),
    ('Gestire utenti', (True, False, False)),
    ('Esportare dati', (True, True, False)),
    ('Accedere ai report', (True, True, True)),
    ('Modificare opportunità', (True, True, False)),
    ('Modificare impostazioni', (True, False, False)),
    ('Visualizzare documenti', (True, True, True)),
    ('Modificare documenti', (True, True, False)),
    ('Eliminare documenti', (True, False, False)),
]


def _base_context(request, active):
    return {
        'active_nav': 'impostazioni',
        'sections': SECTIONS,
        'active_section': active,
    }


@login_required
def utenti(request):
    users = User.objects.select_related('profile').order_by('-is_active', 'first_name', 'last_name')
    org = Organizzazione.current()
    active_count = users.filter(is_active=True).count()
    ctx = _base_context(request, 'utenti')
    ctx.update({
        'users': users,
        'active_count': active_count,
        'org': org,
        'ruoli_choices': Ruolo.choices,
        'permessi_matrice': PERMESSI_MATRICE,
    })
    return render(request, 'settings/utenti.html', ctx)


@require_perm('impostazioni.modifica')
def organizzazione(request):
    org = Organizzazione.current()
    if request.method == 'POST':
        org.nome = request.POST.get('nome', org.nome).strip() or org.nome
        org.piano = request.POST.get('piano', org.piano).strip() or org.piano
        posti = request.POST.get('posti_totali')
        if posti and posti.isdigit():
            org.posti_totali = int(posti)
        org.save()
        messages.success(request, 'Organizzazione aggiornata')
        return redirect('settings:organizzazione')
    ctx = _base_context(request, 'organizzazione')
    ctx['org'] = org
    return render(request, 'settings/organizzazione.html', ctx)


@login_required
def ruoli(request):
    ctx = _base_context(request, 'ruoli')
    ctx.update({
        'ruoli': Ruolo.choices,
        'permessi_matrice': PERMESSI_MATRICE,
    })
    return render(request, 'settings/ruoli.html', ctx)


@require_perm('utenti.gestisci')
@require_POST
def cambia_ruolo(request, pk):
    profile = get_object_or_404(Profile, user_id=pk)
    nuovo = request.POST.get('ruolo')
    if nuovo in dict(Ruolo.choices) and nuovo != profile.ruolo:
        if profile.ruolo == Ruolo.AMMINISTRATORE:
            altri_admin = Profile.objects.filter(
                ruolo=Ruolo.AMMINISTRATORE, user__is_active=True,
            ).exclude(pk=profile.pk).exists()
            if not altri_admin:
                messages.error(
                    request,
                    "Impossibile cambiare ruolo: deve restare almeno un Amministratore attivo.",
                )
                return _user_row_response(request, profile.user)
        profile.ruolo = nuovo
        profile.save(update_fields=['ruolo'])
    return _user_row_response(request, profile.user)


@require_perm('utenti.gestisci')
@require_POST
def utente_toggle_attivo(request, pk):
    user = get_object_or_404(User.objects.select_related('profile'), pk=pk)
    if user.pk == request.user.pk:
        messages.error(request, 'Non puoi modificare lo stato del tuo account.')
        return _user_row_response(request, user)
    user.is_active = not user.is_active
    user.save(update_fields=['is_active'])
    profile = user.profile
    profile.stato = StatoUtente.ATTIVO if user.is_active else StatoUtente.SOSPESO
    profile.save(update_fields=['stato'])
    return _user_row_response(request, user)


@require_POST
def utente_elimina(request, pk):
    if not request.user.is_superuser:
        return HttpResponseForbidden('Solo il superuser può eliminare utenti.')
    user = get_object_or_404(User, pk=pk)
    if user.pk == request.user.pk:
        messages.error(request, 'Non puoi eliminare il tuo stesso account.')
        return _user_row_response(request, user)
    user.delete()
    if getattr(request, 'htmx', None):
        return HttpResponse('')
    return redirect('settings:utenti')


def _user_row_response(request, user):
    if getattr(request, 'htmx', None):
        return render(request, 'settings/_user_row.html', {
            'u': user,
            'ruoli_choices': Ruolo.choices,
        })
    return redirect('settings:utenti')


@require_perm('utenti.gestisci')
def invita_utente(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        first = request.POST.get('first_name', '').strip()
        last = request.POST.get('last_name', '').strip()
        ruolo = request.POST.get('ruolo', Ruolo.ACCOUNT_MANAGER)

        if not email:
            messages.error(request, 'Email obbligatoria')
        elif User.objects.filter(email__iexact=email).exists():
            messages.error(request, 'Utente già presente con questa email')
        elif User.objects.filter(username__iexact=email).exists():
            messages.error(request, 'Esiste già un utente con questo username')
        else:
            password = get_random_string(12)
            user = User.objects.create_user(
                username=email, email=email, first_name=first, last_name=last,
                password=password,
            )
            profile = user.profile
            ruolo_val = ruolo if ruolo in dict(Ruolo.choices) else Ruolo.ACCOUNT_MANAGER
            profile.ruolo = ruolo_val
            profile.stato = StatoUtente.INVITATO
            # dal dialogo della pagina Team
            profile.qualifica = request.POST.get('qualifica', '').strip()[:80]
            profile.reparto = request.POST.get('reparto', '').strip()[:60]
            profile.save()

            sent = _send_invito_email(
                user, password, request.user, dict(Ruolo.choices)[ruolo_val],
            )
            if sent:
                messages.success(request, f'Invito inviato a {email}')
            else:
                messages.warning(
                    request,
                    f'Utente creato ma la mail non è partita — controlla i log. '
                    f'Password temporanea per {email}: {password}',
                )
        prossima = request.POST.get('next', '')
        if url_has_allowed_host_and_scheme(prossima, {request.get_host()}, request.is_secure()):
            return redirect(prossima)
        return redirect('settings:utenti')
    ctx = _base_context(request, 'utenti')
    ctx['ruoli_choices'] = Ruolo.choices
    return render(request, 'settings/invita.html', ctx)


@login_required
@login_required
def fatturazione(request):
    """Impostazioni di fatturazione: la periodicità della liquidazione IVA
    (stima automatica in cashflow/iva.py)."""
    from accounts.permissions import has_perm
    from anagrafiche.models import AnagraficaAzienda
    from cashflow import iva

    az = AnagraficaAzienda.objects.select_related('profilo_fiscale').first()
    if request.method == 'POST':
        if not has_perm(request.user, 'impostazioni.modifica'):
            return HttpResponseForbidden('Permesso negato')
        scelta = request.POST.get('liquidazione_iva', '')
        if scelta not in dict(AnagraficaAzienda.LiquidazioneIva.choices):
            messages.error(request, 'Periodicità non valida.')
        elif az is None:
            messages.error(request, "Compila prima l'anagrafica dell'azienda.")
        elif scelta == az.liquidazione_iva:
            messages.info(request, 'Nessuna modifica.')
        else:
            iva.cambia_periodicita(scelta)
            messages.success(request, {
                'trimestrale': 'Liquidazione IVA trimestrale: stime ricalcolate.',
                'mensile': 'Liquidazione IVA mensile: stime ricalcolate.',
                '': 'Stima della liquidazione IVA spenta: tolte le stime ancora da pagare.',
            }[scelta])
        return redirect('settings:fatturazione')
    ctx = _base_context(request, 'fatturazione')
    regime = az.profilo_fiscale if az and az.profilo_fiscale_id else None
    ctx.update({
        'azienda': az,
        'scelta': az.liquidazione_iva if az else '',
        'regime': regime,
        'senza_iva': bool(regime and regime.regime_fiscale in iva.REGIMI_SENZA_IVA),
        'puo_modificare': has_perm(request.user, 'impostazioni.modifica'),
    })
    return render(request, 'settings/fatturazione.html', ctx)


COLORI_DOCUMENTI_SUGGERITI = [
    ('#5b21b6', 'Viola'),
    ('#1e3a8a', 'Blu notte'),
    ('#0f766e', 'Verde petrolio'),
    ('#7f1d1d', 'Bordeaux'),
    ('#1f2937', 'Antracite'),
]


@require_perm('impostazioni.modifica')
def documenti(request):
    """Logo, intestazione, piè di pagina e colore dei documenti stampati
    (fatture, offerte, ordini, DDT, note di credito). Solo gli
    amministratori (permesso ``impostazioni.modifica``) li vedono e li
    cambiano; ogni modifica finisce nell'audit log."""
    from anagrafiche.forms import IntestazioneDocumentiForm
    from anagrafiche.models import AnagraficaAzienda
    from documenti.esempio import TIPI

    az = AnagraficaAzienda.current()
    if request.method == 'POST':
        form = IntestazioneDocumentiForm(request.POST, request.FILES, instance=az)
        if form.is_valid():
            form.save()
            registra_audit(Azione.UPDATE, request=request, target=az,
                           meta={'sezione': 'Intestazione documenti', 'campi': form.changed_data})
            messages.success(request, 'Intestazione dei documenti aggiornata.')
            return redirect('settings:documenti')
    else:
        form = IntestazioneDocumentiForm(instance=az)
    ctx = _base_context(request, 'documenti')
    ctx.update({
        'form': form,
        'azienda': az,
        'tipi_anteprima': TIPI,
        'colori_suggeriti': COLORI_DOCUMENTI_SUGGERITI,
    })
    return render(request, 'settings/documenti.html', ctx)


@xframe_options_sameorigin          # si mostra in un iframe nella pagina qui sopra
@require_perm('impostazioni.modifica')
def documenti_anteprima(request):
    """PDF di un documento di esempio (``?tipo=fattura|offerta|ddt``) con
    l'intestazione salvata: niente viene scritto."""
    from django.http import Http404

    from anagrafiche.models import AnagraficaAzienda
    from documenti import esempio
    from documenti.utils import crea_pdf

    tipo = request.GET.get('tipo', 'fattura')
    if tipo not in esempio.TIPI:
        raise Http404
    template, ctx = esempio.contesto(tipo, AnagraficaAzienda.objects.first())
    return crea_pdf(template, ctx, f'anteprima-{tipo}.pdf', request=request)


@login_required
def placeholder(request, titolo):
    ctx = _base_context(request, titolo.lower())
    ctx['placeholder_titolo'] = titolo
    return render(request, 'settings/placeholder.html', ctx)


@login_required
def backup(request):
    """Backup completo (database + file): elenco, creazione e download. Solo
    superuser: dentro ci sono anche password PEC e certificato di firma."""
    from . import backup as bk
    ctx = _base_context(request, 'backup')
    ctx['puo'] = request.user.is_superuser
    if request.method == 'POST':
        if not request.user.is_superuser:
            messages.error(request, 'Solo un superuser può creare i backup.')
            return redirect('settings:backup')
        try:
            file = bk.crea(con_media=request.POST.get('media') != '0')
        except Exception as exc:  # disco pieno, permessi, database non SQLite…
            messages.error(request, f'Backup non riuscito: {exc}')
            return redirect('settings:backup')
        registra_audit(Azione.EXPORT, request=request, target_type='Backup', target_label=file.name,
                  meta={'byte': file.stat().st_size})
        messages.success(request, f'Backup creato: {file.name}. Scaricalo e conservalo al sicuro.')
        return redirect('settings:backup')
    ctx['backups'] = bk.elenco() if request.user.is_superuser else []
    ctx['conserva'] = bk.CONSERVA
    return render(request, 'settings/backup.html', ctx)


@login_required
def backup_scarica(request, nome):
    from django.http import FileResponse, Http404
    from . import backup as bk
    if not request.user.is_superuser:
        raise Http404
    file = bk.percorso(nome)
    if file is None:
        raise Http404
    registra_audit(Azione.EXPORT, request=request, target_type='Backup', target_label=f'Download {nome}')
    return FileResponse(file.open('rb'), as_attachment=True, filename=nome)


@require_perm('impostazioni.modifica')
def api_tokens(request):
    """Gestione API token dell'utente corrente + lista webhook.

    POST action=create_token  → crea token (mostrato in chiaro una volta).
    POST action=revoke_token  → body token_id → revoca.
    POST action=create_webhook → crea webhook dai campi form.
    POST action=delete_webhook → body webhook_id.
    """
    action = request.POST.get('action', '')
    new_token_value = None

    if request.method == 'POST':
        if action == 'create_token':
            name = (request.POST.get('name') or 'Token').strip()[:80]
            tok = ApiToken.objects.create(
                user=request.user, name=name,
                scrittura=request.POST.get('scrittura') == '1',
            )
            # in DB c'è solo l'hash: il valore in chiaro si vede ora e mai più
            new_token_value = tok.token_in_chiaro
            messages.success(request, 'Token creato. Copialo ora — non sarà più visibile.')
        elif action == 'revoke_token':
            tid = request.POST.get('token_id')
            ApiToken.objects.filter(pk=tid, user=request.user).update(revoked=True)
            messages.success(request, 'Token revocato.')
        elif action == 'create_webhook':
            name = (request.POST.get('wh_name') or '').strip()[:80]
            url = (request.POST.get('wh_url') or '').strip()
            secret = (request.POST.get('wh_secret') or '').strip()[:64]
            eventi = request.POST.getlist('wh_eventi')
            eventi = [e for e in eventi if e in WebhookEvento.values]
            if not name or not url:
                messages.error(request, 'Nome e URL sono obbligatori.')
            elif len(url) > 300:
                messages.error(request, 'URL troppo lungo (max 300 caratteri).')
            else:
                from api.webhooks import UrlWebhookNonValida, valida_url_webhook
                try:
                    valida_url_webhook(url)
                except UrlWebhookNonValida as e:
                    messages.error(request, f'URL del webhook non ammesso: {e}')
                    return render(request, 'settings/api.html', _api_ctx(request))
                Webhook.objects.create(
                    name=name, url=url, secret=secret, eventi=eventi, attivo=True,
                )
                messages.success(request, 'Webhook creato.')
        elif action == 'delete_webhook':
            wid = request.POST.get('webhook_id')
            Webhook.objects.filter(pk=wid).delete()
            messages.success(request, 'Webhook rimosso.')
        return render(request, 'settings/api.html', _api_ctx(request, new_token_value))

    return render(request, 'settings/api.html', _api_ctx(request))


def _api_ctx(request, new_token_value=None):
    ctx = _base_context(request, 'api')
    ctx.update({
        'tokens': ApiToken.objects.filter(user=request.user).order_by('-created_at'),
        'new_token_value': new_token_value,
        'webhooks': Webhook.objects.all(),
        'eventi_choices': WebhookEvento.choices,
    })
    return ctx


@require_perm('utenti.gestisci')
def audit_log(request):
    """Vista lista dell'audit log con filtri (azione, target_type, q, data).

    Cap 50 righe per pagina.
    """
    qs = AuditLog.objects.all().select_related('actor')

    azione = (request.GET.get('azione') or '').strip()
    if azione:
        qs = qs.filter(azione=azione)

    target_type = (request.GET.get('target_type') or '').strip()
    if target_type:
        qs = qs.filter(target_type=target_type)

    q = (request.GET.get('q') or '').strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(
            Q(actor_label__icontains=q)
            | Q(target_label__icontains=q)
            | Q(target_id__icontains=q),
        )

    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get('page'))

    ctx = _base_context(request, 'audit')
    ctx.update({
        'page_obj': page_obj,
        'paginator': paginator,
        'azione': azione,
        'target_type': target_type,
        'q': q,
        'azioni_choices': Azione.choices,
        'target_types': (
            AuditLog.objects.exclude(target_type='')
            .values_list('target_type', flat=True)
            .distinct()
            .order_by('target_type')
        ),
    })
    return render(request, 'settings/audit.html', ctx)
