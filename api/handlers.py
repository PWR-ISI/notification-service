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


# ── appointment.created handler ───────────────────────────────────────────────
def _get_user_email_from_cognito(patient_id: str) -> str | None:
    """Look up user email in Cognito by sub (patient_id)."""
    import boto3
    import os
    pool_id = os.getenv('COGNITO_USER_POOL_ID', '')
    if not pool_id:
        return None
    try:
        cognito = boto3.client('cognito-idp', region_name=os.getenv('AWS_REGION', 'us-east-1'))
        resp = cognito.list_users(
            UserPoolId=pool_id,
            Filter=f'sub = "{patient_id}"',
        )
        for user in resp.get('Users', []):
            for attr in user.get('Attributes', []):
                if attr['Name'] == 'email':
                    return attr['Value']
    except Exception as exc:
        logger.error("Cognito lookup failed for patient_id=%s: %s", patient_id, exc)
    return None


def _send_email_via_ses(to_email: str, subject: str, body: str) -> None:
    import boto3
    import os
    sender = os.getenv('SENDER_EMAIL', '')
    if not sender:
        logger.warning("SENDER_EMAIL not configured; skipping email to %s", to_email)
        return
    ses = boto3.client('ses', region_name=os.getenv('AWS_REGION', 'us-east-1'))
    ses.send_email(
        Source=sender,
        Destination={'ToAddresses': [to_email]},
        Message={
            'Subject': {'Data': subject, 'Charset': 'UTF-8'},
            'Body': {'Text': {'Data': body, 'Charset': 'UTF-8'}},
        },
    )
    logger.info("Email sent to %s: %s", to_email, subject)


def _schedule_reminder(appointment_id: str, patient_id: str, appointment_time_iso: str, delta_hours: int) -> None:
    """Schedule a reminder via EventBridge Scheduler."""
    import boto3, os
    from datetime import datetime, timezone as tz, timedelta

    try:
        appt_time = datetime.fromisoformat(appointment_time_iso.replace('Z', '+00:00'))
    except Exception:
        logger.error("Cannot parse appointment_time: %s", appointment_time_iso)
        return

    remind_at = appt_time - timedelta(hours=delta_hours)
    now = datetime.now(tz.utc)

    if remind_at <= now:
        logger.info("Reminder %dh for appt %s is in the past, skipping", delta_hours, appointment_id)
        return

    label = f"{delta_hours}h"
    schedule_name = f"appt-{appointment_id[:8]}-{label}"
    sqs_arn = os.getenv('NOTIFICATION_SQS_ARN', '')
    role_arn = os.getenv('SCHEDULER_ROLE_ARN', '')

    if not sqs_arn or not role_arn:
        logger.warning("NOTIFICATION_SQS_ARN or SCHEDULER_ROLE_ARN not set; skipping reminder schedule")
        return

    message = json.dumps({
        'event_type': 'appointment.reminder',
        'payload': {
            'appointment_id': appointment_id,
            'patient_id': patient_id,
            'reminder_type': label,
            'appointment_time': appointment_time_iso,
        }
    })

    try:
        scheduler = boto3.client('scheduler', region_name=os.getenv('AWS_REGION', 'us-east-1'))
        scheduler.create_schedule(
            Name=schedule_name,
            ScheduleExpression=f"at({remind_at.strftime('%Y-%m-%dT%H:%M:%S')})",
            ScheduleExpressionTimezone='UTC',
            FlexibleTimeWindow={'Mode': 'OFF'},
            ActionAfterCompletion='DELETE',
            Target={
                'Arn': sqs_arn,
                'RoleArn': role_arn,
                'Input': message,
            },
        )
        logger.info("Scheduled %s reminder for appt %s at %s", label, appointment_id, remind_at.isoformat())
    except Exception as exc:
        logger.error("EventBridge Scheduler failed for %s-%s: %s", appointment_id, label, exc)


def handle_appointment_created(payload: dict) -> bool:
    """Send immediate confirmation + schedule 24h and 1h reminders."""
    appointment_id = payload.get('appointment_id', '')
    patient_id = payload.get('patient_id', '')
    appointment_time = payload.get('appointment_time', '')
    status = payload.get('status', 'scheduled')

    if not patient_id:
        logger.warning("appointment.created missing patient_id; dropping.")
        return True

    patient_email = _get_user_email_from_cognito(str(patient_id))

    # 1. Immediate confirmation email
    if patient_email:
        from datetime import datetime
        try:
            dt = datetime.fromisoformat(appointment_time.replace('Z', '+00:00'))
            appt_str = dt.strftime('%A, %B %d %Y at %H:%M UTC')
        except Exception:
            appt_str = appointment_time

        subject = "Appointment Confirmed"
        body = (
            f"Your appointment has been successfully booked.\n\n"
            f"Date & Time: {appt_str}\n"
            f"Appointment ID: {appointment_id}\n"
            f"Status: {status}\n\n"
            f"You will receive reminders 24 hours and 1 hour before your appointment.\n"
        )
        try:
            _send_email_via_ses(patient_email, subject, body)
        except Exception as exc:
            logger.error("SES send failed for %s: %s", patient_email, exc)

    # 2. Schedule reminders (24h and 1h before)
    if appointment_time:
        _schedule_reminder(appointment_id, patient_id, appointment_time, delta_hours=24)
        _schedule_reminder(appointment_id, patient_id, appointment_time, delta_hours=1)

    # 3. Save to DB
    try:
        from api.models import Notification
        Notification.objects.create(
            recipient_id=patient_id,
            recipient_email=patient_email or '',
            notification_type='appointment_confirmed',
            channel='email',
            subject='Appointment Confirmed',
            message=f"Appointment {appointment_id} confirmed for {appointment_time}.",
            related_entity_type='appointment',
            related_entity_id=appointment_id,
            sent_at=timezone.now(),
        )
    except Exception as exc:
        logger.error("Failed to save notification to DB: %s", exc)

    return True


def handle_appointment_reminder(payload: dict) -> bool:
    """Send reminder email (triggered by EventBridge Scheduler)."""
    appointment_id = payload.get('appointment_id', '')
    patient_id = payload.get('patient_id', '')
    reminder_type = payload.get('reminder_type', '')
    appointment_time = payload.get('appointment_time', '')

    if not patient_id:
        return True

    patient_email = _get_user_email_from_cognito(str(patient_id))
    if not patient_email:
        logger.warning("No email found for patient_id=%s", patient_id)
        return True

    from datetime import datetime
    try:
        dt = datetime.fromisoformat(appointment_time.replace('Z', '+00:00'))
        appt_str = dt.strftime('%A, %B %d %Y at %H:%M UTC')
    except Exception:
        appt_str = appointment_time

    if reminder_type == '24h':
        subject = "Appointment Reminder – Tomorrow"
        body = f"Reminder: You have an appointment tomorrow.\n\nDate & Time: {appt_str}\nAppointment ID: {appointment_id}\n"
    else:
        subject = "Appointment Reminder – In 1 Hour"
        body = f"Reminder: Your appointment is in 1 hour.\n\nDate & Time: {appt_str}\nAppointment ID: {appointment_id}\n"

    try:
        _send_email_via_ses(patient_email, subject, body)
    except Exception as exc:
        logger.error("SES reminder failed for %s: %s", patient_email, exc)
        raise  # retriable

    try:
        from api.models import Notification
        Notification.objects.create(
            recipient_id=patient_id,
            recipient_email=patient_email,
            notification_type='appointment_reminder',
            channel='email',
            subject=subject,
            message=body,
            related_entity_type='appointment',
            related_entity_id=appointment_id,
            sent_at=timezone.now(),
        )
    except Exception as exc:
        logger.error("Failed to save reminder notification: %s", exc)

    return True


def handle_file_uploaded(payload: dict) -> bool:
    """Notify user when a file is uploaded."""
    file_id = payload.get('file_id', '')
    user_id = payload.get('user_id', '')
    original_name = payload.get('original_name', 'file')

    if not user_id:
        return True

    user_email = _get_user_email_from_cognito(str(user_id))
    if user_email:
        try:
            _send_email_via_ses(
                user_email,
                "File Uploaded Successfully",
                f"Your file '{original_name}' (ID: {file_id}) has been uploaded successfully.",
            )
        except Exception as exc:
            logger.error("SES send failed: %s", exc)

    return True


# ── Registry consumed by api/management/commands/consume_events.py ───────────
HANDLERS: dict[str, Callable[[dict], bool]] = {
    "payment.success": handle_payment_success,
    "appointment.created": handle_appointment_created,
    "appointment.reminder": handle_appointment_reminder,
    "file.uploaded": handle_file_uploaded,
}
