"""
Demo seed for notification-service.
Idempotent: clears the demo patient's notifications and recreates a realistic set
(4 unread + 2 read) plus default preferences.

Run:  docker exec notification-service python seed_demo.py
"""
import os
import datetime
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
django.setup()

from django.utils import timezone  # noqa: E402
from api.models import Notification, NotificationPreference  # noqa: E402

PATIENT_SUB = os.getenv("DEMO_PATIENT_SUB", "bdcb9332-aa94-41c7-a501-c76fdcd157f8")

# (type, channel, subject, message, is_read, minutes_ago)
ITEMS = [
    ("appointment_confirmed", "in_app", "Wizyta potwierdzona",
     "Twoja wizyta u Dr Anna Lekarz została potwierdzona na 17.06.2026, godz. 09:00.", False, 5),
    ("appointment_reminder", "in_app", "Przypomnienie o wizycie",
     "Przypominamy o jutrzejszej wizycie u Dr Anna Lekarz o godz. 09:00. Prosimy o przybycie 10 min wcześniej.", False, 60),
    ("payment_received", "email", "Płatność przyjęta",
     "Otrzymaliśmy płatność 150,00 zł za wizytę lekarską. Dziękujemy!", False, 180),
    ("medical_record_available", "in_app", "Nowy dokument medyczny",
     "Wyniki badań krwi (morfologia) są już dostępne w zakładce Dokumenty medyczne.", False, 240),
    ("appointment_cancelled", "sms", "Wizyta odwołana",
     "Wizyta z dnia 12.06.2026 została odwołana. Możesz umówić nowy termin w portalu.", True, 1440),
    ("general", "in_app", "Witamy w ISI Medical System",
     "Dziękujemy za rejestrację. W portalu umówisz wizytę, sprawdzisz dokumenty i powiadomienia.", True, 4320),
]


def main():
    now = timezone.now()
    Notification.objects.filter(recipient_id=PATIENT_SUB).delete()
    for t, ch, subj, msg, is_read, mins in ITEMS:
        n = Notification.objects.create(
            recipient_id=PATIENT_SUB, recipient_email="patient@example.com",
            notification_type=t, channel=ch, subject=subj, message=msg, is_read=is_read,
            sent_at=now - datetime.timedelta(minutes=mins),
            read_at=(now - datetime.timedelta(minutes=mins - 10)) if is_read else None,
        )
        # auto_now_add ignores assignment on create, so backdate explicitly
        Notification.objects.filter(pk=n.pk).update(created_at=now - datetime.timedelta(minutes=mins))
        print(("READ  " if is_read else "UNREAD") + " | " + subj)

    NotificationPreference.objects.update_or_create(user_id=PATIENT_SUB, defaults=dict(
        email_notifications=True, sms_notifications=True, push_notifications=True,
        appointment_reminders=True, payment_notifications=True, promotional_emails=False))

    unread = Notification.objects.filter(recipient_id=PATIENT_SUB, is_read=False).count()
    print("Seeded %d notifications (%d unread) for %s" % (len(ITEMS), unread, PATIENT_SUB))


if __name__ == "__main__":
    main()
