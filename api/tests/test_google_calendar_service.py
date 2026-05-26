"""
Unit tests for api.services.google_calendar.

The Google API client is mocked at the boundary — we only test our own logic
(encryption round-trip, error mapping, idempotent iCalUID, revocation).
"""
from unittest.mock import patch, MagicMock

import pytest
from django.utils import timezone

from api.models import GoogleCalendarCredential
from api.services import google_calendar as gcal


# ── Encryption ───────────────────────────────────────────────────────────────
@pytest.mark.django_db
def test_encrypt_decrypt_round_trip():
    ct = gcal.encrypt_token("my-refresh-token")
    assert ct != "my-refresh-token"  # actually encrypted, not just passed through
    assert gcal.decrypt_token(ct) == "my-refresh-token"


@pytest.mark.django_db
def test_decrypt_with_bad_key_raises_auth_error(settings):
    ct = gcal.encrypt_token("foo")
    # Rotate the key — the old ciphertext can't be decrypted anymore.
    from cryptography.fernet import Fernet
    settings.GOOGLE_TOKEN_ENCRYPTION_KEY = Fernet.generate_key().decode()
    with pytest.raises(gcal.GoogleCalendarAuthError):
        gcal.decrypt_token(ct)


# ── Credential loading ──────────────────────────────────────────────────────
@pytest.mark.django_db
def test_load_credentials_missing_user():
    with pytest.raises(gcal.GoogleCalendarAuthError):
        gcal.load_credentials_for_user(user_id=999)


@pytest.mark.django_db
def test_load_credentials_revoked_treated_as_missing():
    GoogleCalendarCredential.objects.create(
        user_id=42,
        google_account_email="a@b.com",
        encrypted_refresh_token=gcal.encrypt_token("rt"),
        scopes=[],
        revoked_at=timezone.now(),
    )
    with pytest.raises(gcal.GoogleCalendarAuthError):
        gcal.load_credentials_for_user(user_id=42)


@pytest.mark.django_db
def test_load_credentials_marks_revoked_on_refresh_failure():
    """When Google rejects the refresh token, the DB row is marked revoked."""
    row = GoogleCalendarCredential.objects.create(
        user_id=7,
        google_account_email="x@y.com",
        encrypted_refresh_token=gcal.encrypt_token("rt"),
        scopes=[],
    )
    with patch("api.services.google_calendar.Credentials") as mock_creds_cls:
        instance = MagicMock()
        instance.refresh.side_effect = Exception("invalid_grant")
        mock_creds_cls.return_value = instance

        with pytest.raises(gcal.GoogleCalendarAuthError):
            gcal.load_credentials_for_user(user_id=7)

    row.refresh_from_db()
    assert row.revoked_at is not None


# ── Event creation ───────────────────────────────────────────────────────────
@pytest.mark.django_db
def test_create_event_passes_ical_uid_for_idempotency():
    """external_id → iCalUID so SQS retries don't create duplicate events."""
    GoogleCalendarCredential.objects.create(
        user_id=1,
        google_account_email="patient@example.com",
        encrypted_refresh_token=gcal.encrypt_token("rt"),
        scopes=[],
    )
    spec = gcal.CalendarEventSpec(
        summary="Wizyta", description="...",
        start_iso="2026-06-01T10:00:00+02:00",
        end_iso="2026-06-01T10:30:00+02:00",
        attendees_emails=["patient@example.com", "doctor@example.com"],
        location="Gabinet 3",
        external_id="appointment-555-patient@notification-service.pwr-isi",
    )

    fake_service = MagicMock()
    fake_service.events.return_value.insert.return_value.execute.return_value = {"id": "g123"}

    with patch("api.services.google_calendar.load_credentials_for_user", return_value=MagicMock()), \
         patch("api.services.google_calendar.build", return_value=fake_service):
        result = gcal.create_event(user_id=1, spec=spec)

    assert result == {"id": "g123"}
    insert_kwargs = fake_service.events.return_value.insert.call_args.kwargs
    assert insert_kwargs["body"]["iCalUID"] == spec.external_id
    assert insert_kwargs["body"]["location"] == "Gabinet 3"
    assert insert_kwargs["sendUpdates"] == "all"


# ── Revocation ───────────────────────────────────────────────────────────────
@pytest.mark.django_db
def test_revoke_user_returns_false_when_nothing_to_revoke():
    assert gcal.revoke_user(user_id=999) is False


@pytest.mark.django_db
def test_revoke_user_sets_revoked_at():
    row = GoogleCalendarCredential.objects.create(
        user_id=11,
        google_account_email="x@y.com",
        encrypted_refresh_token=gcal.encrypt_token("rt"),
        scopes=[],
    )
    assert gcal.revoke_user(user_id=11) is True
    row.refresh_from_db()
    assert row.revoked_at is not None
    # Idempotent: second call finds nothing active.
    assert gcal.revoke_user(user_id=11) is False
