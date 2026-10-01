from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.errors import Invalid
from core.models import CafeSettings, WelcomeMessage

# طول هر فیلد متنی تنظیمات؛ برای بریدن ورودی کلاینت به اندازه‌ی ستون مدل.
TEXT_LIMITS = {"name": 120, "address": 400, "phone": 40, "receipt_note": 150}


def cafe_settings_dict(s):
    return {"name": s.name, "address": s.address, "phone": s.phone,
            "receipt_note": s.receipt_note, "auto_print": s.auto_print,
            "low_stock_alert": s.low_stock_alert,
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
