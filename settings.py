import os
from pathlib import Path
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-notification-key')
DEBUG = os.getenv('DEBUG', 'True') == 'True'
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'rest_framework', 'rest_framework_simplejwt', 'corsheaders', 'drf_spectacular', 'api',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware', 'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [], 'APP_DIRS': True,
        'OPTIONS': {'context_processors': [
            'django.template.context_processors.request',
            'django.contrib.auth.context_processors.auth',
            'django.contrib.messages.context_processors.messages',
        ]},
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql' if os.getenv('DB_ENGINE') == 'postgresql' else 'django.db.backends.sqlite3',
        'NAME': os.getenv('DB_NAME', 'notification_db'),
        'USER': os.getenv('DB_USER', 'postgres'),
        'PASSWORD': os.getenv('DB_PASSWORD', 'password'),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
    }
}

if os.getenv('DB_ENGINE') != 'postgresql':
    DATABASES['default']['NAME'] = BASE_DIR / 'db.sqlite3'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE, TIME_ZONE, USE_I18N, USE_TZ = 'en-us', 'UTC', True, True
STATIC_URL, DEFAULT_AUTO_FIELD = 'static/', 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ('rest_framework_simplejwt.authentication.JWTAuthentication',),
    'DEFAULT_PERMISSION_CLASSES': ('rest_framework.permissions.IsAuthenticated',),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 10, 'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

SIMPLE_JWT = {'ACCESS_TOKEN_LIFETIME': timedelta(hours=1), 'ALGORITHM': 'HS256', 'SIGNING_KEY': SECRET_KEY}
CORS_ALLOWED_ORIGINS = os.getenv('CORS_ALLOWED_ORIGINS', 'http://localhost:3000,http://127.0.0.1:3000').split(',')

SPECTACULAR_SETTINGS = {
    'TITLE': 'Notification Service API',
    'DESCRIPTION': 'Notifications, preferences, and Google Calendar integration.',
    'VERSION': '2.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

# ── Domain-event consumer ─────────────────────────────────────────────────────
# SQS queue the background worker drains. Populated by SNS subscriptions
# (payment-service publishes payment.success here via SNS→SQS fan-out).
EVENTS_SQS_QUEUE_URL = os.getenv('EVENTS_SQS_QUEUE_URL', '')

# Sibling services we call to enrich thin events (payment.success carries only
# IDs; we resolve the appointment + parties via these REST endpoints).
APPOINTMENT_SERVICE_URL = os.getenv('APPOINTMENT_SERVICE_URL', 'http://appointment-service:8000')
AUTH_SERVICE_URL        = os.getenv('AUTH_SERVICE_URL',        'http://auth-identity-service:8000')

# Internal service-to-service auth header — apps pass this so calls to the
# above services don't need a per-user JWT. The consuming service validates it.
INTERNAL_SERVICE_TOKEN = os.getenv('INTERNAL_SERVICE_TOKEN', '')

# ── Google Calendar OAuth2 ────────────────────────────────────────────────────
# Created in Google Cloud Console → APIs & Services → Credentials.
# Redirect URI must exactly match what's registered there.
GOOGLE_OAUTH_CLIENT_ID     = os.getenv('GOOGLE_OAUTH_CLIENT_ID', '')
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv('GOOGLE_OAUTH_CLIENT_SECRET', '')
GOOGLE_OAUTH_REDIRECT_URI  = os.getenv('GOOGLE_OAUTH_REDIRECT_URI', 'http://localhost:8003/api/v2/google/callback')
GOOGLE_OAUTH_SCOPES = [
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/userinfo.email',
    'openid',
]

# Fernet key for at-rest encryption of refresh tokens. Generate once with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Production MUST inject this from AWS Secrets Manager; missing key with
# DEBUG=False raises at OAuth callback time (fails loud, refuses plaintext).
GOOGLE_TOKEN_ENCRYPTION_KEY = os.getenv('GOOGLE_TOKEN_ENCRYPTION_KEY', '')

if DEBUG and not GOOGLE_TOKEN_ENCRYPTION_KEY:
    # Dev / pytest convenience: auto-generate an ephemeral key so the OAuth
    # code path doesn't crash. Tokens encrypted with it won't survive a
    # process restart — that's expected in this mode.
    from cryptography.fernet import Fernet as _Fernet
    GOOGLE_TOKEN_ENCRYPTION_KEY = _Fernet.generate_key().decode()

# Calendar IDs — 'primary' targets each user's main calendar. Override for a
# shared clinic calendar in env if desired.
GOOGLE_CALENDAR_ID_DEFAULT = os.getenv('GOOGLE_CALENDAR_ID_DEFAULT', 'primary')
