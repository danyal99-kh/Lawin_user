from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

api_v1 = [
    path("", include("accounts.api_urls")),
    path("", include("tables.api_urls")),
    path("", include("orders.api_urls")),
    path("", include("waiter_calls.api_urls")),
    path("", include("catalog.api_urls")),
    path("", include("core.api_urls")),
    path("", include("inventory.api_urls")),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include(api_v1)),
    path("", include("customer.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
