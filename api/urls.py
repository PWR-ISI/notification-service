from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import NotificationViewSet, NotificationTemplateViewSet, NotificationPreferenceViewSet, HealthCheckView, ingest_event

router = DefaultRouter()
router.register(r'notifications', NotificationViewSet, basename='notifications')
router.register(r'templates', NotificationTemplateViewSet, basename='templates')
router.register(r'preferences', NotificationPreferenceViewSet, basename='preferences')
router.register(r'health', HealthCheckView, basename='health')

urlpatterns = [
    path('events/', ingest_event, name='ingest-event'),
    path('', include(router.urls)),
]
