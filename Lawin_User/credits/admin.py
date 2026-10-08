"""پنل ادمین نسیه‌ها — فقط‌خواندنی.

نسیه، بدهکار و تسویه همه از طریق سرویس‌ها/API نوشته می‌شوند تا قفل ردیفی،
تقدیم قدیمی‌ترین-اول و ثبت دفتر حسابداری با هم بشکند. اگر ادمین بتواند
مستقیم مانده را عوض کند، دفتر از واقعیت جدا می‌شود.
"""

from django.contrib import admin

from core.admin import ReadOnlyAdmin

from .models import Credit, CreditPayment, Debtor


class CreditInline(admin.TabularInline):
    model = Credit
    extra = 0
    can_delete = False
    fields = ("order", "amount", "remaining_amount", "status", "created_at")
    readonly_fields = fields


@admin.register(Debtor)
class DebtorAdmin(ReadOnlyAdmin):
    list_display = ("name", "phone", "created_at")
    search_fields = ("name", "phone")
    inlines = [CreditInline]


@admin.register(Credit)
class CreditAdmin(ReadOnlyAdmin):
    list_display = ("debtor", "order", "amount", "remaining_amount", "status",
                    "created_at")
    list_filter = ("status",)
    search_fields = ("debtor__name",)
    raw_id_fields = ("debtor", "order")


admin.site.register(CreditPayment, ReadOnlyAdmin)
