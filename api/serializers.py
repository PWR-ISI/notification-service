from rest_framework import serializers
from .models import Notification, NotificationTemplate, NotificationPreference, GoogleCalendarCredential


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ('id', 'recipient_id', 'notification_type', 'channel', 'subject', 'message', 'is_read', 'created_at', 'read_at')
        read_only_fields = ('id', 'created_at', 'read_at')


class NotificationTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationTemplate
        fields = ('id', 'name', 'notification_type', 'channel', 'subject', 'template_body', 'variables', 'is_active', 'created_at', 'updated_at')
        read_only_fields = ('id', 'created_at', 'updated_at')


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = ('id', 'user_id', 'email_notifications', 'sms_notifications', 'push_notifications', 'appointment_reminders', 'payment_notifications', 'promotional_emails', 'updated_at')
        read_only_fields = ('id', 'user_id', 'created_at')


class GoogleAuthInitResponseSerializer(serializers.Serializer):
    """Response of GET /google/auth-url — caller follows the URL to consent."""
    authorization_url = serializers.URLField(help_text="Redirect the user here to begin OAuth.")
    state             = serializers.CharField(help_text="Round-tripped to /google/callback; signed proof of user identity.")


class GoogleAuthCallbackQuerySerializer(serializers.Serializer):
    """Query params Google sends to /google/callback."""
    code  = serializers.CharField(required=True, help_text="Single-use authorization code.")
    state = serializers.CharField(required=True, help_text="State value from /google/auth-url.")


class GoogleCalendarCredentialStatusSerializer(serializers.ModelSerializer):
    """Public view of a user's connection status — no secret material."""
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = GoogleCalendarCredential
        fields = ('google_account_email', 'calendar_id', 'scopes', 'revoked_at', 'is_active', 'created_at', 'updated_at')
        read_only_fields = fields
