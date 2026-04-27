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
from django.views.decorators.http import require_POST

from accounts.models import Profile, Ruolo, StatoUtente
from accounts.permissions import require_perm
from api.models import ApiToken, Webhook, WebhookEvento
from audit.models import AuditLog, Azione

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
    if nuovo in dict(Ruolo.choices):
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
        elif User.objects.filter(email=email).exists():
            messages.error(request, 'Utente già presente con questa email')
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
        return redirect('settings:utenti')
    ctx = _base_context(request, 'utenti')
    ctx['ruoli_choices'] = Ruolo.choices
    return render(request, 'settings/invita.html', ctx)


@login_required
def placeholder(request, titolo):
    ctx = _base_context(request, titolo.lower())
    ctx['placeholder_titolo'] = titolo
    return render(request, 'settings/placeholder.html', ctx)


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
            tok = ApiToken.objects.create(user=request.user, name=name)
            new_token_value = tok.token
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
            if not name or not url:
                messages.error(request, 'Nome e URL sono obbligatori.')
            else:
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
    page_obj = paginator.page(request.GET.get('page') or 1)

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
