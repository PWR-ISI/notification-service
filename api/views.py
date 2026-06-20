import logging

from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.conf import settings
from django.utils import timezone
from .models import Notification, NotificationTemplate, NotificationPreference
from .serializers import NotificationSerializer, NotificationTemplateSerializer, NotificationPreferenceSerializer
from .handlers import HANDLERS

logger = logging.getLogger(__name__)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def ingest_event(request):
    """Internal endpoint: other services POST a domain event ({event_type, payload}) here
    and we turn it into a notification via the same HANDLERS the SQS consumer uses.
    Authenticated with the shared internal token (no user JWT)."""
    if request.headers.get('X-Internal-Token') != getattr(settings, 'INTERNAL_SHARED_TOKEN', 'dev-internal-token'):
        return Response({'detail': 'forbidden'}, status=status.HTTP_403_FORBIDDEN)
    event_type = request.data.get('event_type')
    payload = request.data.get('payload') or {}
    handler = HANDLERS.get(event_type)
    if not handler:
        return Response({'status': 'ignored', 'event_type': event_type})
    try:
        handler(payload)
    except Exception as exc:  # noqa: BLE001 - never fail the caller
        logger.error('Handler for %s failed: %s', event_type, exc)
        return Response({'status': 'error', 'detail': str(exc)}, status=status.HTTP_200_OK)
    return Response({'status': 'ok'})


class HealthCheckView(viewsets.ViewSet):
    permission_classes = [AllowAny]

    @action(detail=False, methods=['get'])
    def health(self, request):
        return Response({'status': 'healthy'}, status=status.HTTP_200_OK)


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['is_read', 'notification_type', 'channel']
    ordering = ['-created_at']

    def get_queryset(self):
        user_id = self.request.query_params.get('recipient_id')
        if user_id:
            return Notification.objects.filter(recipient_id=user_id)
        return Notification.objects.filter(recipient_id=self.request.user.id)

    @action(detail=True, methods=['put'])
    def read(self, request, pk=None):
        notification = self.get_object()
        notification.is_read = True
        notification.read_at = timezone.now()
        notification.save()
        return Response(NotificationSerializer(notification).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['put'])
    def mark_all_as_read(self, request):
        user_id = request.query_params.get('recipient_id', request.user.id)
        Notification.objects.filter(recipient_id=user_id, is_read=False).update(
            is_read=True,
            read_at=timezone.now()
        )
        return Response({'status': 'All notifications marked as read'}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        user_id = request.query_params.get('recipient_id', request.user.id)
        count = Notification.objects.filter(recipient_id=user_id, is_read=False).count()
        return Response({'unread_count': count}, status=status.HTTP_200_OK)


class NotificationTemplateViewSet(viewsets.ModelViewSet):
    queryset = NotificationTemplate.objects.filter(is_active=True)
    serializer_class = NotificationTemplateSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['notification_type', 'channel']

    def get_queryset(self):
        if self.request.user.is_staff:
            return NotificationTemplate.objects.all()
        return NotificationTemplate.objects.filter(is_active=True)


class NotificationPreferenceViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['get'])
    def my_preferences(self, request):
        preference, created = NotificationPreference.objects.get_or_create(user_id=request.user.id)
        serializer = NotificationPreferenceSerializer(preference)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['put'])
    def update_preferences(self, request):
        preference, created = NotificationPreference.objects.get_or_create(user_id=request.user.id)
        serializer = NotificationPreferenceSerializer(preference, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
