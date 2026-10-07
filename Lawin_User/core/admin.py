"""پنل ادمین برای مدل‌های مالی عمداً فقط‌خواندنی است.

Purchase/Waste/Order/Payment همگی از طریق سرویس‌ها نوشته می‌شوند تا موجودی،
قفل و دفتر حسابداری با هم هماهنگ بمانند. اگر ادمین بتواند مستقیم سطر بسازد یا
حذف کند، دفتر حسابداری بی‌سروصدا از واقعیت جدا می‌شود. برای دیدن اینکه آیا
دفتر با واقعیت هم‌تراز است از `GET /api/v1/accounting/verify/` استفاده کنید.
"""

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from inventory.models import InventoryItem, InventoryTransaction, Recipe, RecipeItem
from orders.models import Order, Payment

from .models import Expense, WelcomeMessage

READ_ONLY = _("فقط‌خواندنی است؛ این رکورد فقط از طریق API/سرویس‌ها تغییر می‌کند.")


class ReadOnlyAdmin(admin.ModelAdmin):
    """همه‌ی مسیرهای نوشتن را می‌بندد و کاربر را به API هدایت می‌کند."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Django اعتبارسنجی را روی صفت کلاس انجام می‌دهد، پس اینجا پر می‌شود نه در متد.
        self.readonly_fields = [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return True

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        extra_context["description"] = READ_ONLY
        return super().changelist_view(request, extra_context)


admin.site.register(WelcomeMessage)
admin.site.register(Expense, ReadOnlyAdmin)
admin.site.register(InventoryTransaction, ReadOnlyAdmin)
admin.site.register(Order, ReadOnlyAdmin)
admin.site.register(Payment, ReadOnlyAdmin)


class RecipeItemInline(admin.TabularInline):
    model = RecipeItem
    extra = 1


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    inlines = [RecipeItemInline]


@admin.register(InventoryItem)
class InventoryItemAdmin(ReadOnlyAdmin):
    list_display = ("name", "unit", "current_stock", "unit_cost", "min_stock")
    search_fields = ("name",)
