"""
Root pytest conftest.

Env-var-based config (Google OAuth client_id/secret, sibling-service URLs)
isn't set here because pytest-django's `pytest_load_initial_conftests` hook
calls `django.setup()` before any user-conftest hook can register — making
our own hook a chicken-and-egg.

Instead we rely on settings.py providing dev-friendly defaults under DEBUG.
Tests run with DEBUG=True by default (no DEBUG env var), so the Fernet key
auto-generates and the OAuth client_id/secret defaults to empty strings
that the test suite either mocks or doesn't exercise.
"""
import os

# Only thing actually needed — point Django at the right settings module.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
