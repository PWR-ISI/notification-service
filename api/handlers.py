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


def _make(patient_id, appointment_id, ntype, subject, message):
    """Create a notification, idempotent per (appointment, type)."""
    if not patient_id:
        logger.warning("event without patient_id; ignoring.")
        return
    if appointment_id and Notification.objects.filter(
        recipient_id=patient_id,
        related_entity_type="appointment",
        related_entity_id=appointment_id,
        notification_type=ntype,
    ).exists():
        logger.info("Notification (%s) for appointment %s already exists; skipping.", ntype, appointment_id)
        return
    Notification.objects.create(
        recipient_id=patient_id,
        notification_type=ntype,
        channel="in_app",
        subject=subject,
        message=message,
        related_entity_type="appointment",
        related_entity_id=appointment_id,
    )
    logger.info("Created %s notification for patient %s", ntype, patient_id)


def on_appointment_created(payload, envelope=None):
    """appointment-service emits 'appointment.created' after booking a visit."""
    when = _fmt(payload.get("scheduled_start"))
    _make(
        payload.get("patient_id"), payload.get("appointment_id"),
        "appointment_confirmed", "Wizyta umówiona",
        f"Twoja wizyta została umówiona na {when}. Status: oczekuje na płatność.",
    )
    # Also let the doctor know a new visit landed in their calendar.
    _make(
        payload.get("doctor_id"), payload.get("appointment_id"),
        "appointment_confirmed", "Nowa wizyta",
        f"Nowa wizyta została umówiona na {when}.",
    )


def on_appointment_cancelled(payload, envelope=None):
    """appointment-service emits 'appointment.cancelled' when a visit is cancelled."""
    when = _fmt(payload.get("scheduled_start"))
    reason = (payload.get("reason") or "").strip()
    msg = f"Twoja wizyta z dnia {when} została odwołana."
    if reason:
        msg += f" Powód: {reason}."
    msg += " Możesz umówić nowy termin w portalu."
    _make(
        payload.get("patient_id"), payload.get("appointment_id"),
        "appointment_cancelled", "Wizyta odwołana", msg,
    )
    doc_msg = f"Wizyta z dnia {when} została odwołana."
    if reason:
        doc_msg += f" Powód: {reason}."
    _make(
        payload.get("doctor_id"), payload.get("appointment_id"),
        "appointment_cancelled", "Wizyta odwołana", doc_msg,
    )


def on_appointment_paid(payload, envelope=None):
    """appointment-service emits 'appointment.paid' after successful payment."""
    when = _fmt(payload.get("scheduled_start"))
    _make(
        payload.get("patient_id"), payload.get("appointment_id"),
        "payment_received", "Płatność potwierdzona",
        f"Otrzymaliśmy płatność za wizytę z dnia {when}. Wizyta jest potwierdzona.",
    )


def on_appointment_completed(payload, envelope=None):
    """schedule-service emits 'appointment.completed' when a doctor finishes a visit."""
    when = _fmt(payload.get("scheduled_start"))
    summary = (payload.get("visit_summary") or "").strip()
    msg = f"Twoja wizyta z dnia {when} została zakończona."
    if summary:
        msg += f" Podsumowanie: {summary}"
    _make(
        payload.get("patient_id"), payload.get("appointment_id"),
        "appointment_completed", "Wizyta zakończona", msg,
    )


def on_payment_succeeded(payload, envelope=None):
    """payment-service emits 'payment.succeeded' after a confirmed PayU payment."""
    _make(
        payload.get("patient_id"), payload.get("appointment_id"),
        "payment_received", "Płatność potwierdzona",
        "Otrzymaliśmy płatność za Twoją wizytę. Wizyta jest potwierdzona.",
    )


def on_payment_refunded(payload, envelope=None):
    """payment-service emits 'payment.refunded' after a refund for a cancelled visit."""
    _make(
        payload.get("patient_id"), payload.get("appointment_id"),
        "payment_refunded", "Zwrot płatności",
        "Płatność za odwołaną wizytę została zwrócona.",
    )


HANDLERS = {
    "appointment.created": on_appointment_created,
    "appointment.cancelled": on_appointment_cancelled,
    "appointment.paid": on_appointment_paid,
    "appointment.completed": on_appointment_completed,
    "payment.succeeded": on_payment_succeeded,
    "payment.refunded": on_payment_refunded,
}
