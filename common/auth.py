"""
JWT stub authenticator for notification-service.
Decodes JWT from Authorization header WITHOUT signature verification
and returns a StubUser compatible with DRF's standard permission classes.
"""
import jwt
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


class StubUser:
    """Virtual user built from JWT claims — no local database lookup needed."""

    def __init__(self, user_id, role="patient", email=""):
        self.id = user_id
        self.pk = user_id
        self.role = role
        self.email = email
        self.is_authenticated = True
        self.is_active = True
        self.is_anonymous = False
        self.is_staff = role in ("admin", "staff")

    def has_perm(self, perm, obj=None):
        return True

    def has_module_perms(self, label):
        return True

    def __str__(self):
        return f"StubUser({self.id}, role={self.role})"


class JWTStubAuthentication(BaseAuthentication):
    """DRF authenticator that reads sub/role/email from any Bearer JWT.
    Compatible with rest_framework.permissions.IsAuthenticated."""

    def authenticate(self, request):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith("Bearer "):
            return None

        token = auth_header[7:].strip()
        try:
            payload = jwt.decode(
                token,
                options={"verify_signature": False, "verify_exp": False},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationFailed(f"Invalid token: {exc}")

        user_id = payload.get("sub")
        if not user_id:
            return None

        role = payload.get("role", "patient")
        email = payload.get("email", "")
        return (StubUser(user_id, role, email), None)

    def authenticate_header(self, request):
        return "Bearer"
