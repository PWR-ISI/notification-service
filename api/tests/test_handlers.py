"""
End-to-end handler tests with all I/O mocked.

Coverage:
  - JSON Schema validation rejects malformed events without retrying.
  - Enrichment errors propagate (so SQS redelivers).
  - Calendar inserts happen for both patient and doctor when both connected.
  - Skipped silently for parties without a connected Google account.
  - Notification rows are written regardless of Google connection state.
"""
from unittest.mock import patch

import pytest

from api.handlers import handle_payment_success
from api.models import GoogleCalendarCredential, Notification
from api.services import event_enrichment as enrich
from api.services import google_calendar as gcal


VALID_PAYLOAD = {
    "event": "payment.success",
    "payment_id": "pay-1",
    "order_id": "order-1",
    "appointment_id": "555",
    "patient_id": "101",
    "amount": "150.00",
    "currency": "PLN",
    "completed_at": "2026-05-30T12:00:00+02:00",
}

APPOINTMENT = enrich.AppointmentDetail(
    appointment_id=555, patient_id=101, doctor_id=202,
    start_iso="2026-06-01T10:00:00+02:00",
    end_iso="2026-06-01T10:30:00+02:00",
    location="Gabinet 3", notes="Pierwsza wizyta",
)
PATIENT = enrich.UserContact(user_id=101, email="patient@example.com", full_name="Anna Nowak")
DOCTOR  = enrich.UserContact(user_id=202, email="doctor@example.com",  full_name="Jan Kowalski")


def _stub_enrichment(monkeypatch):
    monkeypatch.setattr(enrich, "fetch_appointment", lambda _id: APPOINTMENT)
    monkeypatch.setattr(
        enrich, "fetch_user",
        lambda uid: PATIENT if uid == 101 else DOCTOR,
    )


# ── Schema validation ───────────────────────────────────────────────────────
@pytest.mark.django_db
def test_handler_drops_malformed_payload_without_calling_anything(monkeypatch):
    calls = {"fetched": False}
    monkeypatch.setattr(
        enrich, "fetch_appointment",
        lambda _id: calls.update(fetched=True) or APPOINTMENT,
    )
    # Missing required `event` field.
    bad = dict(VALID_PAYLOAD)
    del bad["event"]
    assert handle_payment_success(bad) is True   # returns True → consumer deletes
    assert calls["fetched"] is False             # never called enrichment


# ── Happy paths ─────────────────────────────────────────────────────────────
@pytest.mark.django_db
def test_handler_creates_events_for_both_connected_parties(monkeypatch):
    _stub_enrichment(monkeypatch)

    GoogleCalendarCredential.objects.create(
        user_id=101, google_account_email="p@gmail.com",
        encrypted_refresh_token=gcal.encrypt_token("rt-p"), scopes=[],
    )
    GoogleCalendarCredential.objects.create(
        user_id=202, google_account_email="d@clinic.com",
        encrypted_refresh_token=gcal.encrypt_token("rt-d"), scopes=[],
    )

    seen = []
    def fake_create(user_id, spec, calendar_id=None):
        seen.append((user_id, spec.external_id, spec.attendees_emails))
        return {"id": f"evt-{user_id}"}

    with patch("api.handlers.gcal.create_event", side_effect=fake_create):
        assert handle_payment_success(VALID_PAYLOAD) is True

    # Two inserts, two distinct iCalUIDs (one per role)
    assert len(seen) == 2
    user_ids = {s[0] for s in seen}
    uids     = {s[1] for s in seen}
    assert user_ids == {101, 202}
    assert len(uids) == 2

    # In-app notifications persisted for both parties
    assert Notification.objects.filter(recipient_id=101, related_entity_id=555).count() == 1
    assert Notification.objects.filter(recipient_id=202, related_entity_id=555).count() == 1


@pytest.mark.django_db
def test_handler_skips_calendar_for_party_without_connection(monkeypatch):
    _stub_enrichment(monkeypatch)
    GoogleCalendarCredential.objects.create(
        user_id=101, google_account_email="p@gmail.com",
        encrypted_refresh_token=gcal.encrypt_token("rt-p"), scopes=[],
    )
    # Doctor (202) not connected.

    seen = []
    with patch("api.handlers.gcal.create_event", side_effect=lambda *a, **kw: seen.append(a[0]) or {"id": "x"}):
        assert handle_payment_success(VALID_PAYLOAD) is True

    assert seen == [101]   # only patient
    # But in-app notification was still written for the doctor:
    assert Notification.objects.filter(recipient_id=202).count() == 1


# ── Defensive checks ───────────────────────────────────────────────────────
@pytest.mark.django_db
def test_handler_rejects_when_event_patient_doesnt_match_appointment(monkeypatch):
    """If payment.success says patient X but the appointment record has patient Y,
    we drop the event (returns True → ack) so we don't enrich the wrong user."""
    monkeypatch.setattr(enrich, "fetch_appointment", lambda _id: APPOINTMENT)
    bad = dict(VALID_PAYLOAD, patient_id="999")  # mismatch
    assert handle_payment_success(bad) is True
    assert Notification.objects.count() == 0


@pytest.mark.django_db
def test_handler_propagates_enrichment_failure_for_retry(monkeypatch):
    """Network / 5xx from sibling services should bubble up so SQS redelivers."""
    def boom(_id):
        raise enrich.EnrichmentError("appointment-service 503")
    monkeypatch.setattr(enrich, "fetch_appointment", boom)

    with pytest.raises(enrich.EnrichmentError):
        handle_payment_success(VALID_PAYLOAD)


@pytest.mark.django_db
def test_handler_propagates_calendar_api_error_for_retry(monkeypatch):
    """Google API quota / 5xx → re-raise so the message redelivers."""
    _stub_enrichment(monkeypatch)
    GoogleCalendarCredential.objects.create(
        user_id=101, google_account_email="p@gmail.com",
        encrypted_refresh_token=gcal.encrypt_token("rt"), scopes=[],
    )
    GoogleCalendarCredential.objects.create(
        user_id=202, google_account_email="d@clinic.com",
        encrypted_refresh_token=gcal.encrypt_token("rt"), scopes=[],
    )

    def fail(*a, **kw):
        raise gcal.GoogleCalendarApiError("quota exceeded")

    with patch("api.handlers.gcal.create_event", side_effect=fail), \
         pytest.raises(gcal.GoogleCalendarApiError):
        handle_payment_success(VALID_PAYLOAD)
