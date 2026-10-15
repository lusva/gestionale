"""
Django settings for gestionale project (Gestionale CRM AIGIS lab).
"""

from pathlib import Path
import os

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _carica_env(percorso: Path) -> None:
    """Legge ``.env`` (KEY=valore per riga) senza sovrascrivere le variabili
    già presenti nell'ambiente: in produzione systemd le carica già
    (EnvironmentFile), in sviluppo basta il file."""
    if not percorso.is_file():
        return
    for riga in percorso.read_text(encoding='utf-8').splitlines():
        riga = riga.strip()
        if not riga or riga.startswith('#') or '=' not in riga:
            continue
        chiave, valore = riga.split('=', 1)
        valore = valore.strip()
        if len(valore) >= 2 and valore[0] == valore[-1] and valore[0] in '"\'':
            valore = valore[1:-1]
        os.environ.setdefault(chiave.strip(), valore)


_carica_env(BASE_DIR / '.env')


def _env_bool(nome: str, predefinito: bool) -> bool:
    valore = os.environ.get(nome)
    if valore is None or valore.strip() == '':
        return predefinito
    return valore.strip().lower() in ('1', 'true', 'yes', 'on')


def _env_lista(nome: str, predefinito: str = '') -> list[str]:
    return [x.strip() for x in os.environ.get(nome, predefinito).split(',') if x.strip()]


# Default sicuro: senza DJANGO_DEBUG=True l'app gira in modalità produzione.
DEBUG = _env_bool('DJANGO_DEBUG', False)

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured(
            'DJANGO_SECRET_KEY è obbligatoria quando DJANGO_DEBUG non è True '
            '(impostala nel file .env del server).')
    SECRET_KEY = 'django-insecure-dev-only-replace-in-production-8sDfh3KjLm2NpQrStVwXyZaBcDeFgH'

ALLOWED_HOSTS = _env_lista('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1')

# ---------------------------------------------------------------------------
# HTTPS dietro nginx (vedi deploy/nginx-gestionale.conf)
# ---------------------------------------------------------------------------
# nginx termina TLS e passa X-Forwarded-Proto: così request.is_secure() è vero.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
# Origini fidate per il CSRF, con schema: https://gestionale.aigislab.ai
CSRF_TRUSTED_ORIGINS = _env_lista('DJANGO_CSRF_TRUSTED_ORIGINS')
SESSION_COOKIE_SECURE = _env_bool('DJANGO_SESSION_COOKIE_SECURE', not DEBUG)
CSRF_COOKIE_SECURE = _env_bool('DJANGO_CSRF_COOKIE_SECURE', not DEBUG)
SECURE_HSTS_SECONDS = int(os.environ.get('DJANGO_SECURE_HSTS_SECONDS', '0' if DEBUG else '31536000'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool('DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS', False)
# Il redirect 80→443 lo fa già nginx; attivabile anche lato Django.
SECURE_SSL_REDIRECT = _env_bool('DJANGO_SECURE_SSL_REDIRECT', False)
SECURE_CONTENT_TYPE_NOSNIFF = True
# Audit: l'IP del client dall'ultimo X-Forwarded-For (quello messo da nginx).
# Solo dietro un proxy fidato, altrimenti chiunque può falsificarlo.
AUDIT_TRUST_X_FORWARDED_FOR = _env_bool('AUDIT_TRUST_X_FORWARDED_FOR', not DEBUG)

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',

    'django_htmx',

    'tenancy',
    'accounts',
    'anagrafiche',
    'clienti',
    'documenti',
    'magazzino',
    'opportunita',
    'attivita',
    'reports',
    'core_settings',
    'dashboard',
    'search',
    'audit',
    'api',
    'cashflow',
    'team',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'tenancy.middleware.AziendaMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
    'audit.middleware.ThreadLocalRequestMiddleware',
]

ROOT_URLCONF = 'gestionale.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core_settings.context_processors.ui_preferences',
                'core_settings.context_processors.user_perms',
                'core_settings.context_processors.sidebar_counts',
            ],
            'builtins': [
                'core_settings.templatetags.ui',
            ],
        },
    },
]

WSGI_APPLICATION = 'gestionale.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            # WAL: le letture non aspettano le scritture (3 worker gunicorn più
            # sdi_pull). Con WAL il database è db.sqlite3 + db.sqlite3-wal: per
            # una copia usa `manage.py backup`, non la copia del solo file.
            'init_command': 'PRAGMA journal_mode=WAL;',
            # La transazione che scrive prende subito il lock: chi arriva dopo
            # aspetta (fino a timeout) invece di fallire con "database is locked".
            'transaction_mode': 'IMMEDIATE',
            'timeout': 20,
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'it'
TIME_ZONE = 'Europe/Rome'
USE_I18N = True
USE_TZ = True

# Italian date format everywhere
DATE_FORMAT = 'd F Y'
SHORT_DATE_FORMAT = 'd/m/Y'
DATETIME_FORMAT = 'd F Y H:i'
SHORT_DATETIME_FORMAT = 'd/m/Y H:i'
FIRST_DAY_OF_WEEK = 1

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    'default': {'BACKEND': 'tenancy.storage.TenantFileSystemStorage'},
    # nomi con hash del contenuto: niente CSS/JS vecchi in cache dopo un deploy
    'staticfiles': {'BACKEND': 'gestionale.storage.StaticConHash'},
}

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'
# Sottocartelle di MEDIA_ROOT non pubbliche: le serve Django dopo login e
# permesso (gestionale/media_views.py); nginx le nega su /media/.
# In produzione il file lo spedisce nginx (X-Accel-Redirect verso la
# location interna MEDIA_PROTETTI_ACCEL, vedi deploy/nginx-gestionale.conf).
MEDIA_PROTETTI_ACCEL = os.environ.get('DJANGO_MEDIA_ACCEL', '' if DEBUG else '/media-protetti/')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'dashboard:home'
LOGOUT_REDIRECT_URL = 'accounts:login'

# Sessione: con "Ricordami" dura 30 giorni, senza dura 1 giorno (anche
# chiudendo il browser), vedi accounts.views.LoginView. Nomi dei cookie
# dedicati: i nomi standard ``sessionid``/``csrftoken`` vengono sovrascritti da
# qualsiasi altra app Django sullo stesso host (i cookie ignorano la porta).
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
SESSION_DURATA_STANDARD = 60 * 60 * 24
SESSION_COOKIE_NAME = 'aigiscrm_sessionid'
CSRF_COOKIE_NAME = 'aigiscrm_csrftoken'


# ---------------------------------------------------------------------------
# Email
# In DEBUG usa il backend console (gli invii finiscono in stdout di runserver).
# In prod leggi le variabili d'ambiente EMAIL_HOST*, DEFAULT_FROM_EMAIL.
# ---------------------------------------------------------------------------
if DEBUG:
    EMAIL_BACKEND = os.environ.get(
        'DJANGO_EMAIL_BACKEND',
        'django.core.mail.backends.console.EmailBackend',
    )
else:
    EMAIL_BACKEND = os.environ.get(
        'DJANGO_EMAIL_BACKEND',
        'django.core.mail.backends.smtp.EmailBackend',
    )
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'localhost')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '25'))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'False').lower() == 'true'
DEFAULT_FROM_EMAIL = os.environ.get(
    'DEFAULT_FROM_EMAIL', 'Gestionale CRM <noreply@aigislab.ai>',
)
# URL usato nelle email per costruire link assoluti (senza request).
SITE_BASE_URL = os.environ.get('SITE_BASE_URL', 'http://localhost:8000')


# ---------------------------------------------------------------------------
# API / webhook
# ---------------------------------------------------------------------------
# Le chiamate HTTP dei webhook in un thread in background (False = nella
# stessa richiesta, dopo il commit: utile nei test).
WEBHOOK_IN_BACKGROUND = _env_bool('WEBHOOK_IN_BACKGROUND', True)
# Solo sviluppo: permette webhook verso localhost / reti private.
WEBHOOK_CONSENTI_IP_PRIVATI = _env_bool('WEBHOOK_CONSENTI_IP_PRIVATI', False)
# Dopo quante ore una Idempotency-Key torna libera (vedi pulisci_idempotenza).
IDEMPOTENZA_ORE = int(os.environ.get('IDEMPOTENZA_ORE', '24'))
