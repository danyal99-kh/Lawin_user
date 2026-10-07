from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.errors import Invalid
from core.models import CafeSettings, CafeStatus, WelcomeMessage

# طول هر فیلد متنی تنظیمات؛ برای بریدن ورودی کلاینت به اندازه‌ی ستون مدل.
TEXT_LIMITS = {"name": 120, "address": 400, "phone": 40, "receipt_note": 150}


def cafe_settings_dict(s):
    return {"name": s.name, "address": s.address, "phone": s.phone,
            "receipt_note": s.receipt_note, "auto_print": s.auto_print,
            "low_stock_alert": s.low_stock_alert,
            "opening_cash": s.opening_cash, "opening_bank": s.opening_bank,
            "updated_at": s.updated_at.isoformat()}


@api_view(["GET", "PATCH"])
def cafe_settings(request):
    """تنظیمات کلی کافه (Flutter). فقط PATCH جزئی است: فیلد نیامده عوض نمی‌شود."""
    s = CafeSettings.load()
    if request.method == "PATCH":
        d = request.data
        for field, limit in TEXT_LIMITS.items():
            if field in d:
                setattr(s, field, str(d[field] or "").strip()[:limit])
        for field in ("auto_print", "low_stock_alert"):
            if field in d:
                if not isinstance(d[field], bool):
                    raise Invalid("مقدار فعال/غیرفعال نامعتبر است.")
                setattr(s, field, d[field])
        # موجودی اولیه‌ی پول؛ پایه‌ی گزارش گردش نقدینگی
        for field in ("opening_cash", "opening_bank"):
            if field in d:
                raw = d[field]
                if isinstance(raw, bool):
                    raise Invalid("موجودی اولیه نامعتبر است.")
                if isinstance(raw, float) and raw.is_integer():
                    raw = int(raw)
                if isinstance(raw, str) and raw.strip().isascii() and raw.strip().isdigit():
                    raw = int(raw.strip())
                if not isinstance(raw, int) or raw < 0 or raw > 10**12 - 1:
                    raise Invalid("موجودی اولیه نامعتبر است.")
                setattr(s, field, raw)
        if not s.name:
            raise Invalid("نام کافه را وارد کنید.")
        s.save()
    return Response(cafe_settings_dict(s))


def welcome_dict(w):
    return {"title": w.title, "message": w.message, "enabled": w.enabled,
            "updated_at": w.updated_at.isoformat()}


@api_view(["GET", "PUT", "PATCH"])
def welcome_settings(request):
    """تنظیمات ← پیام خوشامدگویی (Flutter). قرارداد موجود باید حفظ شود."""
    w = WelcomeMessage.load()
    if request.method != "GET":
        d = request.data
        if "title" in d:
            w.title = str(d["title"]).strip()[:120]
        if "message" in d:
            w.message = str(d["message"]).strip()[:400]
        if "enabled" in d:
            if not isinstance(d["enabled"], bool):
                raise Invalid("مقدار فعال/غیرفعال نامعتبر است.")
            w.enabled = d["enabled"]
        if not w.title:
            raise Invalid("عنوان خوشامدگویی را وارد کنید.")
        w.save()
    return Response(welcome_dict(w))


# عملیات مجاز روی وضعیت کافه؛ فقط همین دو مقدار پذیرفته می‌شود.
CAFE_STATUS_ACTIONS = ("open", "close")


def cafe_status_dict(s):
    """قرارداد پاسخ وضعیت کافه؛ عیناً همان چیزی که Flutter می‌خواند."""
    iso = lambda value: value.isoformat() if value else None
    return {"is_open": s.is_open, "opened_at": iso(s.opened_at),
            "closed_at": iso(s.closed_at)}


def _revoke_request_token(request):
    """توکنِ همین نشست را سمت سرور حذف می‌کند (پایان شیفت)."""
    auth = getattr(request, "auth", None)
    if auth is not None and hasattr(auth, "delete"):
        try:
            auth.delete()
        except Exception:
            pass


@api_view(["GET", "POST"])
@permission_classes([IsAdminUser])
def cafe_status(request):
    """وضعیت باز/بسته بودن کافه (Flutter).

    GET وضعیت فعلی را می‌دهد؛ نوشتن در دیتابیس ندارد تا خواندنِ داشبورد
    سطری نسازد. POST با `action` برابر `open` یا `close` زمان باز/بسته شدن
    را ثبت می‌کند. تکرار همان عملیات چیزی را عوض نمی‌کند تا زمان باز شدن
    یک کافهِ از قبل باز، بی‌دلیل از نو ثبت نشود.

    «بستن کافه» یعنی پایانِ شیفت: توکن همین نشست سمت سرور هم باطل می‌شود تا
    حتی اگر اپ بین بستن کافه و خروج از حساب کرش کند، هیچ نشستِ باقی‌مانده‌ای
    نماند.
    """
    if request.method == "GET":
        return Response(cafe_status_dict(CafeStatus.current()))

    data = request.data
    action = data.get("action") if isinstance(data, dict) else None
    if action not in CAFE_STATUS_ACTIONS:
        raise Invalid("عملیات باز/بسته کردن کافه نامعتبر است.")

    s = CafeStatus.load()
    if action == "open":
        if s.is_open:
            return Response(cafe_status_dict(s))
        s.is_open = True
        s.opened_at = timezone.now()
        s.closed_at = None
    else:
        if s.is_open:
            s.is_open = False
            s.closed_at = timezone.now()
            s.save()
            _revoke_request_token(request)
        return Response(cafe_status_dict(s))
    s.save()
    return Response(cafe_status_dict(s))
