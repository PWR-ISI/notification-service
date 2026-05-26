"""
OAuth endpoint tests — auth-url / callback / status / disconnect.

State signing and credential persistence are exercised end-to-end through
the Django test client. The Google network calls are stubbed.
"""
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import signing
from django.urls import reverse
from rest_framework.test import APIClient

from api.models import GoogleCalendarCredential
from api.services import google_calendar as gcal


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="anna", password="pw")


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


# ── auth-url ────────────────────────────────────────────────────────────────
def test_auth_url_requires_authentication():
    resp = APIClient().get(reverse('google-auth-url'))
    assert resp.status_code in (401, 403)


@pytest.mark.django_db
def test_auth_url_returns_signed_state_and_url(client, user, settings):
    # Stub Google client config so the view doesn't 503 on missing credentials.
    settings.GOOGLE_OAUTH_CLIENT_ID     = "test-client-id"
    settings.GOOGLE_OAUTH_CLIENT_SECRET = "test-client-secret"

    resp = client.get(reverse('google-auth-url'))
    assert resp.status_code == 200
    assert resp.data["authorization_url"].startswith("https://accounts.google.com/")
    data = signing.loads(resp.data["state"], salt="notification-service.google-oauth.state", max_age=600)
    assert data["user_id"] == user.id


@pytest.mark.django_db
def test_auth_url_returns_503_when_oauth_not_configured(client, settings):
    """No client_id configured → 503, not a misleading 200/500."""
    settings.GOOGLE_OAUTH_CLIENT_ID = ""
    settings.GOOGLE_OAUTH_CLIENT_SECRET = ""
    resp = client.get(reverse('google-auth-url'))
    assert resp.status_code == 503


# ── callback ────────────────────────────────────────────────────────────────
@pytest.mark.django_db
def test_callback_rejects_invalid_state():
    resp = APIClient().get(reverse('google-callback'), {"code": "x", "state": "not-a-real-token"})
    assert resp.status_code == 400


@pytest.mark.django_db
def test_callback_exchanges_code_and_persists_encrypted_token(user):
    state = signing.dumps({"user_id": user.id}, salt="notification-service.google-oauth.state")
    fake = gcal.ExchangedCredentials(
        refresh_token="real-refresh-token",
        google_account_email="anna@gmail.com",
        scopes=["https://www.googleapis.com/auth/calendar.events"],
    )
    with patch("api.views.gcal.exchange_code", return_value=fake):
        resp = APIClient().get(reverse('google-callback'), {"code": "auth-code", "state": state})

    assert resp.status_code == 200
    row = GoogleCalendarCredential.objects.get(user_id=user.id)
    assert row.google_account_email == "anna@gmail.com"
    # Must be encrypted, not raw
    assert row.encrypted_refresh_token != "real-refresh-token"
    assert gcal.decrypt_token(row.encrypted_refresh_token) == "real-refresh-token"
    assert row.revoked_at is None


@pytest.mark.django_db
def test_callback_reactivates_previously_revoked_row(user):
    """User disconnected then reconnected — the existing row is reused, not duplicated."""
    GoogleCalendarCredential.objects.create(
        user_id=user.id, google_account_email="old@gmail.com",
        encrypted_refresh_token=gcal.encrypt_token("old-rt"), scopes=[],
        revoked_at="2026-01-01T00:00:00+00:00",
    )
    state = signing.dumps({"user_id": user.id}, salt="notification-service.google-oauth.state")
    fake = gcal.ExchangedCredentials(
        refresh_token="new-rt", google_account_email="new@gmail.com", scopes=[],
    )
    with patch("api.views.gcal.exchange_code", return_value=fake):
        APIClient().get(reverse('google-callback'), {"code": "x", "state": state})

    assert GoogleCalendarCredential.objects.filter(user_id=user.id).count() == 1
    row = GoogleCalendarCredential.objects.get(user_id=user.id)
    assert row.revoked_at is None
    assert row.google_account_email == "new@gmail.com"


# ── status ──────────────────────────────────────────────────────────────────
@pytest.mark.django_db
def test_status_404_when_not_connected(client):
    resp = client.get(reverse('google-status'))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_status_returns_metadata_when_connected(client, user):
    GoogleCalendarCredential.objects.create(
        user_id=user.id, google_account_email="x@y.com",
        encrypted_refresh_token=gcal.encrypt_token("rt"),
        scopes=["scope-a"],
    )
    resp = client.get(reverse('google-status'))
    assert resp.status_code == 200
    assert resp.data["google_account_email"] == "x@y.com"
    assert resp.data["is_active"] is True
    # Never expose the token
    assert "encrypted_refresh_token" not in resp.data


# ── disconnect ──────────────────────────────────────────────────────────────
@pytest.mark.django_db
def test_disconnect_404_when_nothing_to_revoke(client):
    resp = client.delete(reverse('google-disconnect'))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_disconnect_marks_row_revoked(client, user):
    row = GoogleCalendarCredential.objects.create(
        user_id=user.id, google_account_email="x@y.com",
        encrypted_refresh_token=gcal.encrypt_token("rt"),
        scopes=[],
    )
    resp = client.delete(reverse('google-disconnect'))
    assert resp.status_code == 204
    row.refresh_from_db()
    assert row.revoked_at is not None
