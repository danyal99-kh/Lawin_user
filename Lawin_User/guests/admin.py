"""پنل ادمین مهمان‌ها — فقط‌خواندنی.

ثبت و لغو مهمان از مسیر پرداخت/سرویس‌ها انجام می‌شود تا قفل ردیفی و ثبت دفتر
حسابداری با هم بشکنند. اگر ادمین مستقیم رکورد را عوض کند، دفتر از واقعیت جدا
می‌شود.
"""

from django.contrib import admin

from core.admin import ReadOnlyAdmin

from .models import Guest


@admin.register(Guest)
class GuestAdmin(ReadOnlyAdmin):
    list_display = ("name", "order", "amount", "status", "created_at", "cancelled_at")
    list_filter = ("status",)
    search_fields = ("name", "order__number")
    raw_id_fields = ("order",)
