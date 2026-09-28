from django.contrib import admin
from django.utils.html import format_html
from django.conf import settings

from .models import Table, TableSession


@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ("number", "status", "qr_link")
    actions = ["rotate"]

    @admin.display(description="لینک QR")
    def qr_link(self, obj):
        url = f"{settings.PUBLIC_BASE_URL}/menu/table/{obj.public_token}/"
        return format_html('<a href="{}">{}</a>', url, url)

    @admin.action(description="ساخت توکن جدید QR (قدیمی باطل می‌شود)")
    def rotate(self, request, qs):
        for t in qs:
            t.rotate_token()


admin.site.register(TableSession)
