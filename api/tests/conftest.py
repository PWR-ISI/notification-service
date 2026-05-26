"""
Test setup shared across the Google Calendar tests.

CRITICAL: env var setup MUST happen at module-import time, not inside
`pytest_configure()`. pytest-django reads DJANGO_SETTINGS_MODULE and imports
`settings.py` during its own `pytest_configure` hook, which fires before any
user-defined `pytest_configure()` callbacks here. By the time our hook would
run, settings has already been cached with empty values.

Module-level statements in conftest.py execute the moment pytest discovers
the file, which is before pytest-django loads Django settings.
"""
import os

from cryptography.fernet import Fernet

# Stable per-test-session key — generated once per process, fine for tests.
os.environ.setdefault('GOOGLE_TOKEN_ENCRYPTION_KEY', Fernet.generate_key().decode())
os.environ.setdefault('GOOGLE_OAUTH_CLIENT_ID',     'test-client-id')
os.environ.setdefault('GOOGLE_OAUTH_CLIENT_SECRET', 'test-client-secret')
os.environ.setdefault('GOOGLE_OAUTH_REDIRECT_URI',  'http://testserver/api/v2/google/callback')
os.environ.setdefault('APPOINTMENT_SERVICE_URL',    'http://appointment-service:8000')
os.environ.setdefault('AUTH_SERVICE_URL',           'http://auth-identity-service:8000')
os.environ.setdefault('DJANGO_SETTINGS_MODULE',     'settings')
