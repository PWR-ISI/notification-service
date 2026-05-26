import logging

from django.conf import settings
from django.core import signing
from django.shortcuts import redirect
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import GoogleCalendarCredential, Notification, NotificationPreference, NotificationTemplate
from .serializers import (
    GoogleAuthCallbackQuerySerializer,
    GoogleAuthInitResponseSerializer,
    GoogleCalendarCredentialStatusSerializer,
    NotificationPreferenceSerializer,
    NotificationSerializer,
    NotificationTemplateSerializer,
)
from .services import google_calendar as gcal

logger = logging.getLogger(__name__)

# Salt namespaces the django.core.signing token so it can't be confused with
# tokens issued elsewhere in the codebase.
_OAUTH_STATE_SALT = "notification-service.google-oauth.state"
_OAUTH_STATE_MAX_AGE_SECONDS = 600  # 10 minutes; consent flow is short


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


# ─── Google Calendar OAuth2 endpoints ───────────────────────────────────────
class GoogleAuthInitView(APIView):
    """GET /api/v2/google/auth-url — start the OAuth2 flow.

    Returns a URL the frontend should redirect the user to. The `state`
    parameter is a signed token bound to the current user's id, so the
    callback can identify which user just consented without trusting the
    raw query string.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["google-calendar"],
        responses={200: GoogleAuthInitResponseSerializer},
        description="Issue an OAuth2 authorization URL. The caller follows it; "
                    "Google redirects back to /google/callback with `code` and `state`.",
    )
    def get(self, request):
        try:
            state = signing.dumps({"user_id": request.user.id}, salt=_OAUTH_STATE_SALT)
            url   = gcal.build_authorization_url(state=state)
        except gcal.GoogleCalendarConfigError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({"authorization_url": url, "state": state})


class GoogleAuthCallbackView(APIView):
    """GET /api/v2/google/callback?code=...&state=... — finish the OAuth2 flow.

    AllowAny because Google does the redirect with no Authorization header.
    Identity is recovered from the signed `state` parameter.
    """
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["google-calendar"],
        parameters=[
            OpenApiParameter("code",  str, OpenApiParameter.QUERY, required=True),
            OpenApiParameter("state", str, OpenApiParameter.QUERY, required=True),
        ],
        responses={
            302: OpenApiResponse(description="Redirect back to the frontend on success."),
            400: OpenApiResponse(description="Invalid or expired state, or Google returned an error."),
        },
        description="Exchange the OAuth code for a refresh token and persist it.",
    )
    def get(self, request):
        qs = GoogleAuthCallbackQuerySerializer(data=request.query_params)
        qs.is_valid(raise_exception=True)

        try:
            data = signing.loads(qs.validated_data["state"], salt=_OAUTH_STATE_SALT, max_age=_OAUTH_STATE_MAX_AGE_SECONDS)
        except signing.SignatureExpired:
            return Response({"detail": "OAuth state expired; restart the flow."}, status=400)
        except signing.BadSignature:
            return Response({"detail": "Invalid OAuth state."}, status=400)

        user_id = int(data["user_id"])
        try:
            exchanged = gcal.exchange_code(qs.validated_data["code"])
        except (gcal.GoogleCalendarConfigError, gcal.GoogleCalendarAuthError) as exc:
            logger.warning("OAuth exchange failed for user %s: %s", user_id, exc)
            return Response({"detail": str(exc)}, status=400)

        # Upsert: replace any existing row (including a revoked one) so the
        # user can reconnect cleanly.
        GoogleCalendarCredential.objects.update_or_create(
            user_id=user_id,
            defaults={
                "google_account_email":   exchanged.google_account_email,
                "encrypted_refresh_token": gcal.encrypt_token(exchanged.refresh_token),
                "scopes":                 exchanged.scopes,
                "revoked_at":             None,
            },
        )

        # Frontend deep-link target. If you want to render JSON instead, swap
        # for `return Response({"connected": True, ...})`.
        redirect_target = getattr(settings, 'GOOGLE_OAUTH_SUCCESS_REDIRECT', None)
        if redirect_target:
            return redirect(redirect_target)
        return Response({"connected": True, "email": exchanged.google_account_email})


class GoogleStatusView(APIView):
    """GET /api/v2/google/status — does the current user have an active grant?"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["google-calendar"],
        responses={
            200: GoogleCalendarCredentialStatusSerializer,
            404: OpenApiResponse(description="No credential row for the user."),
        },
    )
    def get(self, request):
        try:
            row = GoogleCalendarCredential.objects.get(user_id=request.user.id)
        except GoogleCalendarCredential.DoesNotExist:
            return Response({"connected": False}, status=status.HTTP_404_NOT_FOUND)
        return Response(GoogleCalendarCredentialStatusSerializer(row).data)


class GoogleDisconnectView(APIView):
    """DELETE /api/v2/google/disconnect — mark the current user's credential revoked."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["google-calendar"],
        responses={
            204: OpenApiResponse(description="Revoked."),
            404: OpenApiResponse(description="Nothing to disconnect."),
        },
    )
    def delete(self, request):
        revoked = gcal.revoke_user(request.user.id)
        if not revoked:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)
