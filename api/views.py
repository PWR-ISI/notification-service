from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.utils import timezone
from .models import Notification, NotificationTemplate, NotificationPreference
from .serializers import NotificationSerializer, NotificationTemplateSerializer, NotificationPreferenceSerializer


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
