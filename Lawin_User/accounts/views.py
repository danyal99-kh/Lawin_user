from rest_framework.authtoken.models import Token
from rest_framework.authtoken.serializers import AuthTokenSerializer
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from core.errors import DomainError


class Unauthorized(DomainError):
    status, code = 401, "unauthorized"


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([ScopedRateThrottle])
def login(request):
    """ورود ادمین. {"token": "...", "user": {...}} — توکن برای REST و WebSocket استفاده می‌شود."""
    s = AuthTokenSerializer(data=request.data, context={"request": request})
    if not s.is_valid():
        raise Unauthorized("نام کاربری یا رمز عبور درست نیست.")
    user = s.validated_data["user"]
    if not user.is_staff:
        raise Unauthorized("این حساب دسترسی مدیریت ندارد.")
    token, _ = Token.objects.get_or_create(user=user)
    return Response({"token": token.key, "user": {"id": user.id, "username": user.username}})


login.cls.throttle_scope = "login"


@api_view(["POST"])
@throttle_classes([ScopedRateThrottle])
def logout(request):
    """خروج کامل: توکن همین نشست سمت سرور حذف می‌شود.

    بعد از خروج، حتی اگر توکن جایی لو رفته‌باشد دیگر کاربری ندارد.
    """
    auth = getattr(request, "auth", None)
    if auth is not None and hasattr(auth, "delete"):
        try:
            auth.delete()
        except Exception:
            pass
    return Response({})


logout.cls.throttle_scope = "logout"
