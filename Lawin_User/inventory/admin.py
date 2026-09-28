from django.contrib import admin
from .models import InventoryItem, InventoryTransaction, Recipe, RecipeItem


class RecipeItemInline(admin.TabularInline):
    model = RecipeItem
    extra = 1


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    inlines = [RecipeItemInline]


admin.site.register(InventoryItem)
admin.site.register(InventoryTransaction)
