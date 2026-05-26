"""
Event-enrichment: fetch missing fields from sibling services.

The `payment.success` event from payment-service carries IDs only:
    payment_id, order_id, appointment_id, patient_id, amount, currency, completed_at

Google Calendar needs date, time, doctor identity, and emails — we resolve
those by calling appointment-service and auth-identity-service over REST.

These helpers are intentionally thin and timeout-bound. The handler treats
any enrichment failure as retriable (leaves the SQS message for redelivery).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5  # seconds


class EnrichmentError(RuntimeError):
    """Sibling service unavailable, returned an error, or response malformed."""


# ── Data classes (decouple from sibling response shapes) ─────────────────────
@dataclass
class AppointmentDetail:
    appointment_id: int
    patient_id: int
    doctor_id: int
    start_iso: str            # ISO 8601 with timezone offset
    end_iso: str
    location: Optional[str]
    notes: Optional[str]


@dataclass
class UserContact:
    user_id: int
    email: str
    full_name: str


# ── Internals ────────────────────────────────────────────────────────────────
def _service_headers() -> dict:
    headers = {'Accept': 'application/json'}
    if settings.INTERNAL_SERVICE_TOKEN:
        headers['X-Internal-Service-Token'] = settings.INTERNAL_SERVICE_TOKEN
    return headers


def _get(url: str) -> dict:
    try:
        resp = requests.get(url, headers=_service_headers(), timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise EnrichmentError(f"Network error calling {url}: {exc}") from exc

    if resp.status_code == 404:
        raise EnrichmentError(f"Not found: {url}")
    if resp.status_code >= 500:
        raise EnrichmentError(f"Upstream 5xx ({resp.status_code}) from {url}")
    if resp.status_code >= 400:
        raise EnrichmentError(f"Upstream {resp.status_code} from {url}: {resp.text[:200]}")

    try:
        return resp.json()
    except ValueError as exc:
        raise EnrichmentError(f"Non-JSON response from {url}") from exc


# ── Public API ───────────────────────────────────────────────────────────────
def fetch_appointment(appointment_id: int) -> AppointmentDetail:
    """Call appointment-service for the full appointment payload.

    Expected response shape (excerpted): {
      "id": int, "patient_id": int, "doctor_id": int,
      "start_time": ISO8601, "end_time": ISO8601,
      "location": str|null, "notes": str|null, ...
    }
    """
    url = f"{settings.APPOINTMENT_SERVICE_URL.rstrip('/')}/api/v2/appointments/{appointment_id}/"
    data = _get(url)

    required = {'id', 'patient_id', 'doctor_id', 'start_time', 'end_time'}
    missing = required - set(data)
    if missing:
        raise EnrichmentError(f"Appointment {appointment_id} missing fields: {missing}")

    return AppointmentDetail(
        appointment_id=int(data['id']),
        patient_id=int(data['patient_id']),
        doctor_id=int(data['doctor_id']),
        start_iso=str(data['start_time']),
        end_iso=str(data['end_time']),
        location=data.get('location'),
        notes=data.get('notes'),
    )


def fetch_user(user_id: int) -> UserContact:
    """Resolve a user's email and display name via auth-identity-service.

    Expected response shape: {"id": int, "email": str, "first_name": str,
                              "last_name": str, ...}
    """
    url = f"{settings.AUTH_SERVICE_URL.rstrip('/')}/api/v2/users/{user_id}/"
    data = _get(url)

    if not data.get('email'):
        raise EnrichmentError(f"User {user_id} has no email in auth-service response")

    full = " ".join(filter(None, [data.get('first_name', ''), data.get('last_name', '')])).strip()
    return UserContact(
        user_id=int(data['id']),
        email=str(data['email']),
        full_name=full or data['email'],
    )
