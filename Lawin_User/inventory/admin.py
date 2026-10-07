"""Recipe و InventoryItem اینجا ثبت می‌شوند. خود `InventoryTransaction` (خرید،
مصرف، ضایعات) در `core/admin.py` فقط‌خواندنی است تا موجودی و دفتر حسابداری فقط از
مسیر سرویس‌ها تغییر کند."""

from django.contrib import admin

from .models import InventoryItem, InventoryTransaction, Recipe, RecipeItem

__all__ = ["InventoryItem", "InventoryTransaction", "Recipe", "RecipeItem"]

assert admin  # نگه‌داشتن ایمپورت برای ابزارهای تحلیل استاتیک
