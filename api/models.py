from django.db import models

class Notification(models.Model):
    NOTIFICATION_TYPES = (
        ('appointment_confirmed', 'Appointment Confirmed'),
        ('appointment_cancelled', 'Appointment Cancelled'),
        ('appointment_reminder', 'Appointment Reminder'),
        ('payment_received', 'Payment Received'),
        ('payment_failed', 'Payment Failed'),
        ('medical_record_available', 'Medical Record Available'),
        ('system_alert', 'System Alert'),
        ('general', 'General Notification'),
    )

    CHANNELS = (
        ('email', 'Email'),
        ('sms', 'SMS'),
        ('push', 'Push Notification'),
        ('in_app', 'In-App'),
    )

    recipient_id = models.CharField(max_length=64, db_index=True)
    recipient_email = models.EmailField(blank=True)
    recipient_phone = models.CharField(max_length=20, blank=True)
    notification_type = models.CharField(max_length=50, choices=NOTIFICATION_TYPES)
    channel = models.CharField(max_length=20, choices=CHANNELS)
    subject = models.CharField(max_length=255)
    message = models.TextField()
    related_entity_type = models.CharField(max_length=50, blank=True)
    related_entity_id = models.CharField(max_length=64, null=True, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient_id', 'is_read']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"{self.notification_type} to {self.recipient_id}"


class NotificationTemplate(models.Model):
    name = models.CharField(max_length=100, unique=True)
    notification_type = models.CharField(max_length=50)
    channel = models.CharField(max_length=20)
    subject = models.CharField(max_length=255)
    template_body = models.TextField()
    variables = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'notification_templates'
        unique_together = ('notification_type', 'channel')

    def __str__(self):
        return self.name


class NotificationPreference(models.Model):
    user_id = models.CharField(max_length=64, unique=True, db_index=True)
    email_notifications = models.BooleanField(default=True)
    sms_notifications = models.BooleanField(default=True)
    push_notifications = models.BooleanField(default=True)
    appointment_reminders = models.BooleanField(default=True)
    payment_notifications = models.BooleanField(default=True)
    promotional_emails = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'notification_preferences'

    def __str__(self):
        return f"Preferences for user {self.user_id}"
