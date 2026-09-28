from django.db import models


class InventoryItem(models.Model):
    class Unit(models.TextChoices):
        GRAM = "g", "گرم"
        MILLILITER = "ml", "میلی‌لیتر"
        PIECE = "piece", "عدد"

    name = models.CharField(max_length=60, unique=True)
    unit = models.CharField(max_length=6, choices=Unit.choices, default=Unit.PIECE)  # واحد پایه
    current_stock = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    min_stock = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    description = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(current_stock__gte=0), name="stock_non_negative")]

    def __str__(self): return self.name


class Recipe(models.Model):
    """دستور مصرف یک واحد از محصول."""
    product = models.OneToOneField("catalog.Product", on_delete=models.CASCADE, related_name="recipe")


class RecipeItem(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="items")
    inventory_item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="recipe_items")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)  # به واحد پایه‌ی کالا

    class Meta:
        constraints = [models.UniqueConstraint(fields=["recipe", "inventory_item"], name="uniq_recipe_item")]


class InventoryTransaction(models.Model):
    """دفتر حرکت موجودی: هر تغییر موجودی یک ردیف (مقدار علامت‌دار) دارد."""
    class Kind(models.TextChoices):
        PURCHASE = "purchase", "خرید"
        WASTE = "waste", "ضایعات"
        CONSUME = "order_consume", "مصرف سفارش"
        RESTORE = "order_restore", "بازگشت (لغو سفارش)"
        ADJUST = "adjust", "اصلاح"

    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=15, choices=Kind.choices)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    order = models.ForeignKey("orders.Order", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="inventory_transactions")
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
