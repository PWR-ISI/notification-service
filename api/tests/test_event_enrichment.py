"""
Tests for api.services.event_enrichment — HTTP calls to sibling services.

We stub `requests.get` rather than spinning up an HTTP server. The point is
the response-handling and error mapping, not network correctness.
"""
from unittest.mock import patch, MagicMock

import pytest
import requests

from api.services import event_enrichment as enrich


def _fake_response(status_code=200, json_data=None, text="ok"):
    r = MagicMock()
    r.status_code = status_code
    r.text = text
    r.json.return_value = json_data or {}
    if status_code >= 400 and not json_data:
        r.json.side_effect = ValueError("not json")
    return r


# ── fetch_appointment ───────────────────────────────────────────────────────
def test_fetch_appointment_happy_path():
    payload = {
        "id": 555, "patient_id": 101, "doctor_id": 202,
        "start_time": "2026-06-01T10:00:00+02:00",
        "end_time":   "2026-06-01T10:30:00+02:00",
        "location": "Gabinet 3", "notes": "First visit",
    }
    with patch("api.services.event_enrichment.requests.get", return_value=_fake_response(200, payload)):
        appt = enrich.fetch_appointment(555)
    assert appt.appointment_id == 555
    assert appt.location == "Gabinet 3"


def test_fetch_appointment_404_raises():
    with patch("api.services.event_enrichment.requests.get", return_value=_fake_response(404)):
        with pytest.raises(enrich.EnrichmentError, match="Not found"):
            enrich.fetch_appointment(999)


def test_fetch_appointment_5xx_raises():
    with patch("api.services.event_enrichment.requests.get", return_value=_fake_response(503)):
        with pytest.raises(enrich.EnrichmentError, match="5xx"):
            enrich.fetch_appointment(1)


def test_fetch_appointment_missing_required_field_raises():
    # `doctor_id` absent — should surface as EnrichmentError, not KeyError.
    payload = {"id": 1, "patient_id": 2, "start_time": "x", "end_time": "y"}
    with patch("api.services.event_enrichment.requests.get", return_value=_fake_response(200, payload)):
        with pytest.raises(enrich.EnrichmentError, match="missing fields"):
            enrich.fetch_appointment(1)


def test_fetch_appointment_network_error_is_wrapped():
    with patch("api.services.event_enrichment.requests.get", side_effect=requests.Timeout("slow")):
        with pytest.raises(enrich.EnrichmentError, match="Network error"):
            enrich.fetch_appointment(1)


# ── fetch_user ──────────────────────────────────────────────────────────────
def test_fetch_user_builds_full_name():
    payload = {"id": 101, "email": "anna@example.com", "first_name": "Anna", "last_name": "Nowak"}
    with patch("api.services.event_enrichment.requests.get", return_value=_fake_response(200, payload)):
        u = enrich.fetch_user(101)
    assert u.full_name == "Anna Nowak"
    assert u.email == "anna@example.com"


def test_fetch_user_falls_back_to_email_when_name_missing():
    payload = {"id": 1, "email": "x@y.com"}
    with patch("api.services.event_enrichment.requests.get", return_value=_fake_response(200, payload)):
        u = enrich.fetch_user(1)
    assert u.full_name == "x@y.com"


def test_fetch_user_rejects_response_without_email():
    payload = {"id": 1, "first_name": "X"}
    with patch("api.services.event_enrichment.requests.get", return_value=_fake_response(200, payload)):
        with pytest.raises(enrich.EnrichmentError, match="no email"):
            enrich.fetch_user(1)
