from rest_framework import serializers
from .models import Notification, NotificationTemplate, NotificationPreference


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
