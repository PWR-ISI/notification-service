"""
Event dispatch: SQS message → handler.

Mirrors the pattern in appointment-service/common/management/commands/
consume_events.py: a `HANDLERS` registry mapping `event_type` strings to
callables. The SQS consumer command imports `HANDLERS` and dispatches.

A handler returns True on success (consumer deletes the SQS message) or
raises to signal a retriable failure (consumer leaves the message for
SQS redelivery up to the queue's max-receive count).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from django.utils import timezone
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaValidationError

from api.models import GoogleCalendarCredential, Notification
from api.services import event_enrichment as enrich
from api.services import google_calendar as gcal

logger = logging.getLogger(__name__)

SCHEMA_DIR = Path(__file__).parent / "schemas"


def _load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


_PAYMENT_SUCCESS_VALIDATOR = Draft202012Validator(_load_schema("payment_success.schema.json"))


# ── payment.success handler ──────────────────────────────────────────────────
def handle_payment_success(payload: dict) -> bool:
    """React to a confirmed payment by adding the visit to patient + doctor calendars.

    Flow:
      1. Validate payload against JSON Schema (drop on schema fail — bad data
         will fail every retry).
      2. Enrich: appointment-service for the appointment, auth-service for
         patient + doctor email (raised errors are retriable; SQS will redeliver).
      3. For each party with an active Google credential, insert a calendar
         event. Use `iCalUID` derived from appointment_id so re-deliveries
         don't create duplicates.
      4. Always persist a `Notification` row (for the in-app feed) even when
         the user hasn't connected Google.
    """
    try:
        _PAYMENT_SUCCESS_VALIDATOR.validate(payload)
    except SchemaValidationError as exc:
        # Schema failures are NOT retriable — the message is malformed.
        logger.error("payment.success failed JSON Schema: %s; payload=%r", exc.message, payload)
        return True  # tell consumer to delete; don't loop on garbage

    appointment_id = int(payload["appointment_id"])
    patient_id     = int(payload["patient_id"])

    appointment = enrich.fetch_appointment(appointment_id)
    if appointment.patient_id != patient_id:
        # Defensive: if event payload says patient X but appointment record
        # has patient Y, something is wrong upstream. Treat as non-retriable.
        logger.error(
            "payment.success patient_id mismatch: event=%s appointment=%s",
            patient_id, appointment.patient_id,
        )
        return True

    patient = enrich.fetch_user(appointment.patient_id)
    doctor  = enrich.fetch_user(appointment.doctor_id)

    spec_for_patient = gcal.CalendarEventSpec(
        summary=f"Wizyta lekarska — dr {doctor.full_name}",
        description=_format_description(payload, appointment, doctor),
        start_iso=appointment.start_iso,
        end_iso=appointment.end_iso,
        attendees_emails=[patient.email, doctor.email],
        location=appointment.location,
        external_id=_ical_uid(appointment_id, "patient"),
    )
    spec_for_doctor = gcal.CalendarEventSpec(
        summary=f"Pacjent: {patient.full_name}",
        description=_format_description(payload, appointment, patient),
        start_iso=appointment.start_iso,
        end_iso=appointment.end_iso,
        attendees_emails=[patient.email, doctor.email],
        location=appointment.location,
        external_id=_ical_uid(appointment_id, "doctor"),
    )

    for party, spec in [(patient, spec_for_patient), (doctor, spec_for_doctor)]:
        _try_create_calendar_event(party.user_id, spec)
        _persist_in_app_notification(
            recipient_id=party.user_id,
            recipient_email=party.email,
            appointment_id=appointment_id,
        )

    return True


# ── Helpers ──────────────────────────────────────────────────────────────────
def _try_create_calendar_event(user_id: int, spec: gcal.CalendarEventSpec) -> None:
    """Best-effort Google insert. Auth/config errors don't block the rest."""
    if not GoogleCalendarCredential.objects.filter(user_id=user_id, revoked_at__isnull=True).exists():
        logger.info("Skipping calendar insert: user %s has no Google credential.", user_id)
        return
    try:
        result = gcal.create_event(user_id, spec)
        logger.info("Inserted calendar event %s for user %s", result.get('id'), user_id)
    except gcal.GoogleCalendarConfigError:
        # Service misconfigured globally — log and skip; don't fail the handler
        # since other users might still work in mixed environments.
        logger.exception("Google Calendar not configured; skipping insert for user %s", user_id)
    except gcal.GoogleCalendarAuthError:
        # The user's grant was revoked — already marked as such in load_credentials.
        logger.warning("User %s Google grant invalid; skipping.", user_id)
    except gcal.GoogleCalendarApiError:
        # Quota / transient. Re-raise so SQS redelivers and we retry.
        logger.exception("Calendar API error for user %s; will retry.", user_id)
        raise


def _persist_in_app_notification(*, recipient_id: int, recipient_email: str, appointment_id: int) -> None:
    Notification.objects.create(
        recipient_id=recipient_id,
        recipient_email=recipient_email,
        notification_type='appointment_confirmed',
        channel='in_app',
        subject='Wizyta potwierdzona',
        message=f"Wizyta #{appointment_id} została potwierdzona i dodana do Twojego kalendarza.",
        related_entity_type='appointment',
        related_entity_id=appointment_id,
        sent_at=timezone.now(),
    )


def _format_description(payload: dict, appointment: enrich.AppointmentDetail, counterparty: enrich.UserContact) -> str:
    bits = [
        f"Płatność: {payload['amount']} {payload['currency']} (id: {payload['payment_id']})",
        f"Z: {counterparty.full_name} ({counterparty.email})",
    ]
    if appointment.notes:
        bits.append(f"Notatki: {appointment.notes}")
    return "\n".join(bits)


def _ical_uid(appointment_id: int, role: str) -> str:
    # iCalUID must be globally unique. Combining appointment id + role gives
    # us idempotency across retries while keeping patient and doctor entries
    # separate (each is on a different calendar).
    return f"appointment-{appointment_id}-{role}@notification-service.pwr-isi"


# ── Registry consumed by api/management/commands/consume_events.py ───────────
HANDLERS: dict[str, Callable[[dict], bool]] = {
    "payment.success": handle_payment_success,
}
