"""
Google Calendar service — OAuth2 flow and event creation.

OAuth2 model: per-user authorization (each patient/doctor consents once,
their refresh_token is stored encrypted in `GoogleCalendarCredential`).
The short-lived access_token is NEVER persisted; it's derived from the
refresh_token on each API call.

This module deliberately does no DB writes in the OAuth helpers — it
returns the data and lets the calling view persist it. That keeps unit
testing trivial (no DB setup needed for cert-exchange tests).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.utils import timezone
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from api.models import GoogleCalendarCredential

logger = logging.getLogger(__name__)


# ── Custom exceptions ────────────────────────────────────────────────────────
class GoogleCalendarConfigError(RuntimeError):
    """OAuth client_id/secret or encryption key missing in settings."""


class GoogleCalendarAuthError(RuntimeError):
    """User has no active credentials, or Google rejected the refresh token."""


class GoogleCalendarApiError(RuntimeError):
    """Calendar API call failed (network, quota, permission)."""


# ── Encryption helpers ───────────────────────────────────────────────────────
def _fernet() -> Fernet:
    """Build the Fernet box from settings.

    Raises if no key is configured — we'd rather refuse than store refresh
    tokens in plaintext.
    """
    key = settings.GOOGLE_TOKEN_ENCRYPTION_KEY
    if not key:
        raise GoogleCalendarConfigError(
            "GOOGLE_TOKEN_ENCRYPTION_KEY is empty; refusing to handle refresh tokens."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        # Most common cause: encryption key was rotated and an old row hasn't
        # been re-encrypted. Surface as auth error so the user re-consents.
        raise GoogleCalendarAuthError("Stored token cannot be decrypted (key rotated?)") from exc


# ── OAuth2 flow ──────────────────────────────────────────────────────────────
def _flow() -> Flow:
    if not (settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET):
        raise GoogleCalendarConfigError("GOOGLE_OAUTH_CLIENT_ID/SECRET not configured.")
    client_config = {
        "web": {
            "client_id":     settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.GOOGLE_OAUTH_REDIRECT_URI],
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=settings.GOOGLE_OAUTH_SCOPES,
        redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI,
    )


def build_authorization_url(state: str) -> str:
    """Return the URL to which we redirect the user to consent.

    `state` is round-tripped to the callback and MUST encode the user_id (or
    a signed proxy for it) so the callback knows which user just consented.
    The view layer is responsible for signing/verifying it.
    """
    flow = _flow()
    url, _ = flow.authorization_url(
        access_type='offline',          # gives us a refresh_token
        include_granted_scopes='true',
        prompt='consent',               # force the consent screen so we always
                                        # get a refresh_token (Google only sends
                                        # it the first time otherwise)
        state=state,
    )
    return url


@dataclass
class ExchangedCredentials:
    """What a successful code exchange yields. View layer persists this."""
    refresh_token: str
    google_account_email: str
    scopes: list[str]


def exchange_code(code: str) -> ExchangedCredentials:
    """Trade an OAuth code (from the callback) for a refresh token + email."""
    flow = _flow()
    flow.fetch_token(code=code)
    creds: Credentials = flow.credentials
    if not creds.refresh_token:
        # Happens when the user previously consented and Google didn't issue
        # a new refresh_token. The `prompt='consent'` flag above is meant to
        # prevent this — if we still hit it the OAuth screen wasn't re-shown.
        raise GoogleCalendarAuthError(
            "Google returned no refresh_token. Revoke the existing grant and retry."
        )

    # The id_token rides along on the response and contains the user's email.
    # google-auth's Credentials exposes it as `id_token` (raw JWT) or via the
    # standard userinfo endpoint. Use userinfo for simplicity.
    email = _fetch_userinfo_email(creds)
    return ExchangedCredentials(
        refresh_token=creds.refresh_token,
        google_account_email=email,
        scopes=list(creds.scopes or []),
    )


def _fetch_userinfo_email(creds: Credentials) -> str:
    """Call Google's OIDC userinfo endpoint to get the authenticated email."""
    import requests
    resp = requests.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=5,
    )
    if resp.status_code != 200:
        raise GoogleCalendarAuthError(
            f"Userinfo endpoint returned {resp.status_code}: {resp.text}"
        )
    payload = resp.json()
    email = payload.get('email')
    if not email:
        raise GoogleCalendarAuthError("Userinfo response had no `email`.")
    return email


# ── Credential resolution ────────────────────────────────────────────────────
def load_credentials_for_user(user_id: int) -> Credentials:
    """Build a fresh google.oauth2.Credentials for the user.

    Raises GoogleCalendarAuthError if the user has no active credential row.
    The returned Credentials auto-refreshes when used by the API client.
    """
    try:
        row = GoogleCalendarCredential.objects.get(user_id=user_id, revoked_at__isnull=True)
    except GoogleCalendarCredential.DoesNotExist as exc:
        raise GoogleCalendarAuthError(
            f"User {user_id} has not connected a Google account."
        ) from exc

    refresh_token = decrypt_token(row.encrypted_refresh_token)
    creds = Credentials(
        token=None,                    # will be refreshed lazily on first call
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
        scopes=row.scopes or settings.GOOGLE_OAUTH_SCOPES,
    )
    # Eagerly refresh so we fail fast and surface auth errors here rather
    # than deeper in the event-creation call.
    try:
        creds.refresh(GoogleAuthRequest())
    except Exception as exc:
        # Token might have been revoked from the Google side — flag the row
        # so future events skip this user until they reconnect.
        row.revoked_at = timezone.now()
        row.save(update_fields=['revoked_at', 'updated_at'])
        raise GoogleCalendarAuthError(
            f"Google rejected refresh token for user {user_id}; row marked revoked."
        ) from exc
    return creds


# ── Event creation ───────────────────────────────────────────────────────────
@dataclass
class CalendarEventSpec:
    """Input to create_event — POPO so handlers don't depend on Google SDK shapes."""
    summary: str
    description: str
    start_iso: str           # ISO 8601 with timezone, e.g. 2026-06-01T10:00:00+02:00
    end_iso: str
    attendees_emails: list[str]
    location: Optional[str] = None
    # External reference so we can de-dupe on retry (Google's `iCalUID` is
    # globally unique; same value → upsert, not duplicate).
    external_id: Optional[str] = None


def create_event(user_id: int, spec: CalendarEventSpec, calendar_id: Optional[str] = None) -> dict:
    """Insert an event on the user's calendar and return Google's response.

    Idempotency: when `spec.external_id` is set we pass it as `iCalUID`. If the
    same UID already exists on the calendar, Google returns the existing event
    (no duplicate). Retries from SQS therefore won't spam calendars.
    """
    creds = load_credentials_for_user(user_id)
    service = build('calendar', 'v3', credentials=creds, cache_discovery=False)

    event_body = {
        'summary': spec.summary,
        'description': spec.description,
        'start': {'dateTime': spec.start_iso},
        'end':   {'dateTime': spec.end_iso},
        'attendees': [{'email': e} for e in spec.attendees_emails],
        'reminders': {'useDefault': True},
    }
    if spec.location:
        event_body['location'] = spec.location
    if spec.external_id:
        event_body['iCalUID'] = spec.external_id

    target_calendar = calendar_id or _resolve_calendar_id(user_id)
    try:
        return service.events().insert(
            calendarId=target_calendar,
            body=event_body,
            sendUpdates='all',     # notify attendees by email
        ).execute()
    except HttpError as exc:
        raise GoogleCalendarApiError(
            f"Google Calendar API returned {exc.resp.status}: {exc.error_details}"
        ) from exc


def _resolve_calendar_id(user_id: int) -> str:
    try:
        row = GoogleCalendarCredential.objects.get(user_id=user_id, revoked_at__isnull=True)
        return row.calendar_id or settings.GOOGLE_CALENDAR_ID_DEFAULT
    except GoogleCalendarCredential.DoesNotExist:
        return settings.GOOGLE_CALENDAR_ID_DEFAULT


def revoke_user(user_id: int) -> bool:
    """Mark the user's credential row revoked. Returns True if a row was updated."""
    updated = GoogleCalendarCredential.objects.filter(
        user_id=user_id, revoked_at__isnull=True
    ).update(revoked_at=timezone.now())
    return updated > 0
