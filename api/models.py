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

    recipient_id = models.IntegerField(db_index=True)
    recipient_email = models.EmailField(blank=True)
    recipient_phone = models.CharField(max_length=20, blank=True)
    notification_type = models.CharField(max_length=50, choices=NOTIFICATION_TYPES)
    channel = models.CharField(max_length=20, choices=CHANNELS)
    subject = models.CharField(max_length=255)
    message = models.TextField()
    related_entity_type = models.CharField(max_length=50, blank=True)
    related_entity_id = models.IntegerField(null=True, blank=True)
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
    user_id = models.IntegerField(unique=True, db_index=True)
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


class GoogleCalendarCredential(models.Model):
    """
    Per-user OAuth2 credentials for Google Calendar.

    The refresh token is encrypted at rest using Fernet (symmetric AES). The
    key lives in settings.GOOGLE_TOKEN_ENCRYPTION_KEY and is injected via env
    var (AWS Secrets Manager in prod). We deliberately do NOT store the
    short-lived access token — it's re-derived from the refresh token on each
    use, keeping the blast radius of a DB leak narrower.

    `revoked_at` is set when the user disconnects or Google rejects the
    refresh token (e.g. user revoked the grant in their Google account). A
    revoked row is kept for audit instead of being deleted.
    """
    user_id              = models.IntegerField(unique=True, db_index=True)
    google_account_email = models.EmailField()
    # Fernet ciphertexts are bytes; TextField stores the base64-urlsafe
    # representation as written by Fernet.encrypt().
    encrypted_refresh_token = models.TextField()
    calendar_id          = models.CharField(max_length=255, default='primary')
    scopes               = models.JSONField(default=list)
    revoked_at           = models.DateTimeField(null=True, blank=True)
    created_at           = models.DateTimeField(auto_now_add=True)
    updated_at           = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'google_calendar_credentials'

    def __str__(self):
        state = 'revoked' if self.revoked_at else 'active'
        return f"GCal[{state}] user={self.user_id} ({self.google_account_email})"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
