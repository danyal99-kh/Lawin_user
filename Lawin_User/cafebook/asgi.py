import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cafebook.settings")
from django.core.asgi import get_asgi_application

django_asgi = get_asgi_application()  # باید قبل از import مدل‌ها/consumerها باشد

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402
from channels.sessions import SessionMiddlewareStack  # noqa: E402
from django.urls import path  # noqa: E402

from core.consumers import AdminConsumer, CustomerConsumer  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi,
    "websocket": SessionMiddlewareStack(URLRouter([
        # ادمین (Flutter) Origin ندارد و با Token احراز هویت می‌شود.
        path("ws/admin/", AdminConsumer.as_asgi()),
        # مشتری (مرورگر): Origin باید یکی از ALLOWED_HOSTS باشد + نشست Django.
        path("ws/customer/", AllowedHostsOriginValidator(CustomerConsumer.as_asgi())),
    ])),
})
