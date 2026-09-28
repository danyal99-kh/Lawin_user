from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.errors import Invalid
from core.models import WelcomeMessage


def welcome_dict(w):
    return {"title": w.title, "message": w.message, "enabled": w.enabled,
            "updated_at": w.updated_at.isoformat()}


@api_view(["GET", "PUT", "PATCH"])
def welcome_settings(request):
    """تنظیمات ← پیام خوشامدگویی (Flutter)."""
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
