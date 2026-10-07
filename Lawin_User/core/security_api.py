"""API رمز امنیتی بخش‌های مالی.

- `POST /security/verify/` — {"password": "..."} → {"ticket": "..."}
  بلیت زمان‌دار برای دسترسی به /accounting/* و /reports/*.
- `POST /security/password/` — {"current_password": ?, "new_password": "..."}
  اولین بار (بدون هش) بدون رمز فعلی؛ از آن به بعد رمز فعلی لازم است.

هر دو endpoint فقط برای ادمین (IsAdminUser پیش‌فرض) و با محدودیت نرخ‌اند تا
رمز امنیتی با تست بی‌نهایت نشکند.
"""

from rest_framework.decorators import api_view, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from core.errors import Invalid, SecurityDenied, SecurityNotConfigured
from core.models import SecuritySettings
from core import security

MIN_LENGTH = 6


def _password(value):
    if not isinstance(value, str):
        raise Invalid("رمز امنیتی صحیح نیست.")
    return value


def _require_new(raw):
    if len(raw) < MIN_LENGTH:
        raise Invalid(f"رمز امنیتی باید حداقل {MIN_LENGTH} کاراکتر باشد.")
    return raw


@api_view(["POST"])
@throttle_classes([ScopedRateThrottle])
def verify(request):
    """ورود امنیتی به بخش‌های مالی؛ موفقیت → بلیت یک‌باره و زمان‌دار."""
    body = request.data if isinstance(request.data, dict) else {}
    raw = body.get("password")
    if not security.is_configured():
        raise SecurityNotConfigured()
    # هر ورودیِ غیرمعتبر (خالی، غلط، غیرمتن) فقط یک پیام عمومی می‌دهد تا
    # وجود و درستی رمز لو نرود.
    if not isinstance(raw, str) or not security.verify_password(raw):
        raise SecurityDenied()
    return Response({"ticket": security.issue_ticket(request.user.id)})


verify.cls.throttle_scope = "security"


@api_view(["POST"])
@throttle_classes([ScopedRateThrottle])
def change_password(request):
    """تنظیم/تغییر رمز امنیتی؛ با هر تغییر، بلیت‌های قبلی فوراً باطل می‌شوند."""
    body = request.data if isinstance(request.data, dict) else {}
    current = body.get("current_password")
    current = current if isinstance(current, str) and current else None
    new_password = _require_new(_password(body.get("new_password")))

    s = SecuritySettings.current()
    if s.is_configured:
        if current is None or not security.verify_password(current):
            raise SecurityDenied()
    elif current is not None:
        # برای راه‌اندازی اولیه نیازی به رمز فعلی نیست؛ فرستادن آن هم اشکالی ندارد.
        pass

    security.set_password(new_password)
    return Response({"updated_at": SecuritySettings.load().updated_at.isoformat()})


change_password.cls.throttle_scope = "security"