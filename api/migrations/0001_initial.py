# Generated as part of the Google Calendar integration work.
# Covers the three pre-existing models (Notification, NotificationTemplate,
# NotificationPreference) which previously lacked a migration, plus the new
# GoogleCalendarCredential.
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('recipient_id', models.IntegerField(db_index=True)),
                ('recipient_email', models.EmailField(blank=True, max_length=254)),
                ('recipient_phone', models.CharField(blank=True, max_length=20)),
                ('notification_type', models.CharField(choices=[
                    ('appointment_confirmed', 'Appointment Confirmed'),
                    ('appointment_cancelled', 'Appointment Cancelled'),
                    ('appointment_reminder', 'Appointment Reminder'),
                    ('payment_received', 'Payment Received'),
                    ('payment_failed', 'Payment Failed'),
                    ('medical_record_available', 'Medical Record Available'),
                    ('system_alert', 'System Alert'),
                    ('general', 'General Notification'),
                ], max_length=50)),
                ('channel', models.CharField(choices=[
                    ('email', 'Email'), ('sms', 'SMS'),
                    ('push', 'Push Notification'), ('in_app', 'In-App'),
                ], max_length=20)),
                ('subject', models.CharField(max_length=255)),
                ('message', models.TextField()),
                ('related_entity_type', models.CharField(blank=True, max_length=50)),
                ('related_entity_id', models.IntegerField(blank=True, null=True)),
                ('is_read', models.BooleanField(db_index=True, default=False)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('read_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'notifications',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='Notification',
            index=models.Index(fields=['recipient_id', 'is_read'], name='notif_recip_read_idx'),
        ),
        migrations.AddIndex(
            model_name='Notification',
            index=models.Index(fields=['created_at'], name='notif_created_idx'),
        ),
        migrations.CreateModel(
            name='NotificationTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100, unique=True)),
                ('notification_type', models.CharField(max_length=50)),
                ('channel', models.CharField(max_length=20)),
                ('subject', models.CharField(max_length=255)),
                ('template_body', models.TextField()),
                ('variables', models.JSONField(default=list)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'notification_templates',
                'unique_together': {('notification_type', 'channel')},
            },
        ),
        migrations.CreateModel(
            name='NotificationPreference',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('user_id', models.IntegerField(db_index=True, unique=True)),
                ('email_notifications', models.BooleanField(default=True)),
                ('sms_notifications', models.BooleanField(default=True)),
                ('push_notifications', models.BooleanField(default=True)),
                ('appointment_reminders', models.BooleanField(default=True)),
                ('payment_notifications', models.BooleanField(default=True)),
                ('promotional_emails', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'db_table': 'notification_preferences'},
        ),
        migrations.CreateModel(
            name='GoogleCalendarCredential',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('user_id', models.IntegerField(db_index=True, unique=True)),
                ('google_account_email', models.EmailField(max_length=254)),
                ('encrypted_refresh_token', models.TextField()),
                ('calendar_id', models.CharField(default='primary', max_length=255)),
                ('scopes', models.JSONField(default=list)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'db_table': 'google_calendar_credentials'},
        ),
    ]
