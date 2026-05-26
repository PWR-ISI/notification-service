from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    GoogleAuthCallbackView,
    GoogleAuthInitView,
    GoogleDisconnectView,
    GoogleStatusView,
    HealthCheckView,
    NotificationPreferenceViewSet,
    NotificationTemplateViewSet,
    NotificationViewSet,
)

router = DefaultRouter()
router.register(r'notifications', NotificationViewSet, basename='notifications')
router.register(r'templates', NotificationTemplateViewSet, basename='templates')
router.register(r'preferences', NotificationPreferenceViewSet, basename='preferences')
router.register(r'health', HealthCheckView, basename='health')

urlpatterns = [
    path('', include(router.urls)),

    # Google Calendar OAuth2 — kept outside the router because they're APIViews,
    # not ViewSets, and have different lifecycles than CRUD endpoints.
    path('google/auth-url/',   GoogleAuthInitView.as_view(),     name='google-auth-url'),
    path('google/callback/',   GoogleAuthCallbackView.as_view(), name='google-callback'),
    path('google/status/',     GoogleStatusView.as_view(),       name='google-status'),
    path('google/disconnect/', GoogleDisconnectView.as_view(),   name='google-disconnect'),
]
