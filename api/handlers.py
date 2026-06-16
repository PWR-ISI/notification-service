"""
Event handlers for notification-service.

The consume_events command looks up an event_type in HANDLERS and invokes the
matching handler. Handlers must be idempotent: SQS may redeliver the same event.
Each handler turns a domain event from another service into a Notification row.
"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from api.models import Notification

logger = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo("Europe/Warsaw")


def _fmt(iso_str):
    """Format an ISO timestamp (stored in UTC) in local Polish time."""
    try:
        dt = datetime.fromisoformat((iso_str or "").replace("Z", "+00:00"))
        return dt.astimezone(LOCAL_TZ).strftime("%d.%m.%Y, %H:%M")
    except Exception:
        return iso_str or ""


def on_appointment_created(payload, envelope=None):
    """appointment-service emits 'appointment.created' after booking a visit."""
    patient_id = payload.get("patient_id")
    if not patient_id:
        logger.warning("appointment.created without patient_id; ignoring.")
        return

    appointment_id = payload.get("appointment_id")
    # Idempotency: don't duplicate if we already notified about this appointment.
    if appointment_id and Notification.objects.filter(
        recipient_id=patient_id,
        related_entity_type="appointment",
        related_entity_id=appointment_id,
    ).exists():
        logger.info("Notification for appointment %s already exists; skipping.", appointment_id)
        return

    when = _fmt(payload.get("scheduled_start"))
    Notification.objects.create(
        recipient_id=patient_id,
        notification_type="appointment_confirmed",
        channel="in_app",
        subject="Wizyta umówiona",
        message=f"Twoja wizyta została umówiona na {when}. Status: oczekuje na płatność.",
        related_entity_type="appointment",
        related_entity_id=appointment_id,
    )
    logger.info("Created notification for patient %s (appointment.created)", patient_id)


HANDLERS = {
    "appointment.created": on_appointment_created,
}
