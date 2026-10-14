from django.contrib import admin

from .models import Category, Offer, Product

admin.site.register(Category)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("name",)


@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = ("scope", "product", "category", "percent", "is_active",
                    "starts_at", "ends_at", "created_at")
    list_filter = ("scope", "is_active")
    search_fields = ("product__name", "category__name")
