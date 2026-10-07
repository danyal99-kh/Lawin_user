"""رمز امنیتی بخش‌های مالی و بلیت امنیتی کوتاه‌مدت آن.

- رمز فقط به‌صورت هش Django (PBKDF2 پیش‌فرض) ذخیره می‌شود؛ متن آن هرگز ذخیره/لاگ
  نمی‌شود.
- ورود موفق به بخش مالی، یک «بلیت امنیتی» امضاشده و زمان‌دار می‌دهد؛ این بلیت در
  حافظه‌ی کلاینت نگه داشته می‌شود و با هدر `X-Security-Ticket` برای همه‌ی
  endpointهای مالی فرستاده می‌شود. بلیت به صاحبش (user id) و به `updated_at` رمز
  گره خورده است؛ پس با تغییر رمز یا استفاده توسط ادمینِ دیگر، فوراً باطل است.
- اعتبار بلیت‌ها «لغزان» است: هر پاسخ موفق از بخش مالی یک بلیت تازه هم‌راه با هدر
  پاسخ می‌فرستد تا کلاینت تا وقتی فعال است منقضی نشود؛ فقط نشستِ بیکارِ بیش از
  [TICKET_TTL] منقضی می‌شود.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from rest_framework.permissions import BasePermission

from core.errors import SecurityDenied

# مدت اعتبار بلیت امنیتی (برای نشست بیکار).
TICKET_TTL = timedelta(seconds=getattr(settings, "SECURITY_TICKET_TTL", 30 * 60))

_PURPOSE = "financial"
_signer = TimestampSigner(key="lawin-security-ticket")

# هدری که کلاینت بلیت را با آن می‌فرستد.
TICKET_HEADER = "X-Security-Ticket"


def is_configured() -> bool:
    from core.models import SecuritySettings

    return SecuritySettings.current().is_configured


def set_password(raw_password: str) -> None:
    """هش PBKDF2 را جایگزین می‌کند؛ متن رمز هیچ‌جا ذخیره نمی‌شود.

    تغییر `updated_at` باعث می‌شود بلیت‌های قبلی (که به نسخه‌ی قدیمی گره
    خورده‌اند) در همان لحظه نامعتبر شوند.
    """
    from core.models import SecuritySettings

    s = SecuritySettings.load()
    s.security_hash = make_password(raw_password)
    s.save()


def verify_password(raw_password: str) -> bool:
    """بررسی رمز در برابر هش ذخیره‌شده (بدون افشای اینکه چند بار اشتباه شد)."""
    from core.models import SecuritySettings

    s = SecuritySettings.current()
    if not s.is_configured:
        return False
    return bool(raw_password) and check_password(raw_password, s.security_hash)


def _read(ticket: str):
    """بلیت را می‌خواند و اگر معتبر نبود None می‌دهد.

    چهار شرط: امضا و زمان صحیح، منظورِ مالی، گره‌خوردن به نسخه‌ی فعلی رمز و
    مالک‌داری نادرستِ قابل‌بررسی (u عدد). بررسی «صاحب بلیت» با کاربرِ درخواست
    در permission انجام می‌شود.
    """
    from core.models import SecuritySettings

    try:
        payload = _signer.unsign_object(ticket, max_age=TICKET_TTL)
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None
    if payload.get("p") != _PURPOSE:
        return None
    if not isinstance(payload.get("u"), int) or payload["u"] < 1:
        return None
    s = SecuritySettings.current()
    if not s.is_configured:
        return None
    try:
        created_at = float(payload.get("v"))
    except (TypeError, ValueError):
        return None
    if abs(created_at - s.updated_at.timestamp()) >= 1e-6:
        return None
    return payload


def issue_ticket(user_id: int) -> str:
    """بلیت زمان‌دار و امضاشده برای دسترسی به بخش‌های مالی."""
    from core.models import SecuritySettings

    s = SecuritySettings.current()
    payload = {"u": user_id, "p": _PURPOSE, "v": str(s.updated_at.timestamp())}
    return _signer.sign_object(payload)


def refresh_ticket(current_ticket: str) -> str:
    """بلیت معتبر را جایگزین می‌کند (نشست لغزان)."""
    payload = _read(current_ticket)
    if payload is None:
        raise SecurityDenied()
    return _signer.sign_object(payload)


def verify_user(ticket: str, user_id: int) -> bool:
    """آیا این بلیت متعلق به همین کاربر است؟"""
    payload = _read(ticket)
    return payload is not None and payload["u"] == user_id


class HasSecurityTicket(BasePermission):
    """دسترسی فقط با بلیت امنیتی معتبرِ همین کاربر در هدر `X-Security-Ticket`.

    روی همه‌ی endpointهای مالی (حسابداری، هزینه‌ها، گزارش‌ها) اعمال می‌شود تا
    بازبینی «رمز امنیتی» هیچ‌وقت فقط به UI ختم نشود.
    """

    message = "رمز امنیتی صحیح نیست."

    def has_permission(self, request, view):
        ticket = request.headers.get(TICKET_HEADER) or ""
        user = getattr(request, "user", None)
        if not ticket or user is None or not verify_user(ticket, user.id):
            raise SecurityDenied()
        return True