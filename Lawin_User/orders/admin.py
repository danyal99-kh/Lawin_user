from django.contrib import admin
from .models import Order, OrderItem, Payment


class ItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("number", "table", "status", "total", "created_at")
    list_filter = ("status", "source")
    inlines = [ItemInline]


admin.site.register(Payment)
