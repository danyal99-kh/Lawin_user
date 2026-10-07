"""ثبت Order/Payment در پنل ادمین در `core/admin.py` انجام می‌شود و عمداً
فقط‌خواندنی است (سرویس‌ها تنها مسیر نوشتن هستند تا موجودی و دفتر حسابداری
با هم هماهنگ بمانند)."""

from django.contrib import admin

from .models import Order, OrderItem, Payment

__all__ = ["Order", "OrderItem", "Payment"]

assert admin  # نگه‌داشتن ایمپورت برای ابزارهای تحلیل استاتیک
