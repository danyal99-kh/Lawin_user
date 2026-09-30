"""دستور مصرف (Recipe): خواندن همه‌ی محصولات همراه با دستورشان و جایگزینی کامل دستور یک محصول.
قالب خروجی دقیقاً مطابق Recipe.fromJson / RecipeItem.fromJson در Flutter است."""

from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from catalog.models import Product
from core.errors import Invalid, NotFound

from .models import InventoryItem, Recipe, RecipeItem

MAX_ITEMS = 30
MAX_QTY = Decimal("99999999.999")  # max_digits=14, decimal_places=3
STEP = Decimal("0.001")


def recipe_dict(product, items):
    return {
        "product_id": product.id,
        "product_name": product.name,
        "items": [
            {
                "inventory_item_id": ri.inventory_item_id,
                "inventory_item_name": ri.inventory_item.name,
                "unit": ri.inventory_item.unit,  # g | ml | piece = BaseUnit.apiValue
                "quantity": float(ri.quantity),  # Decimal → float
            }
            for ri in items
        ],
    }


@api_view(["GET"])
def recipes(request):
    """همه‌ی محصولات؛ محصول بدون دستور مصرف با items=[] برمی‌گردد."""
    by_product = defaultdict(list)
    for ri in RecipeItem.objects.select_related("inventory_item", "recipe").order_by(
        "recipe__product_id", "id"
    ):
        by_product[ri.recipe.product_id].append(ri)
    return Response(
        [
            recipe_dict(p, by_product[p.id])
            for p in Product.objects.order_by("sort_order", "id")
        ]
    )


def _parse_id(raw):
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise Invalid("کالای انبار انتخاب‌شده نامعتبر است.")
    try:
        return int(raw)
    except ValueError:
        raise Invalid("کالای انبار انتخاب‌شده نامعتبر است.")


def _parse_quantity(raw):
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise Invalid("مقدار مصرفی نامعتبر است.")
    try:
        q = Decimal(str(raw).strip())
    except InvalidOperation:
        raise Invalid("مقدار مصرفی نامعتبر است.")
    if not q.is_finite() or q > MAX_QTY:
        raise Invalid("مقدار مصرفی نامعتبر است.")
    q = q.quantize(STEP)
    if q <= 0:
        raise Invalid("مقدار مصرفی باید بیشتر از صفر باشد.")
    return q


def _clean_items(data):
    raw = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise Invalid("فهرست اقلام دستور مصرف نامعتبر است.")
    if len(raw) > MAX_ITEMS:
        raise Invalid("تعداد اقلام دستور مصرف بیش از حد مجاز است.")
    seen, rows = set(), []
    for r in raw:
        if not isinstance(r, dict):
            raise Invalid("فهرست اقلام دستور مصرف نامعتبر است.")
        iid = _parse_id(r.get("inventory_item_id"))
        qty = _parse_quantity(r.get("quantity"))
        if iid in seen:
            raise Invalid("هر کالای انبار فقط یک‌بار قابل افزودن است.")
        seen.add(iid)
        rows.append((iid, qty))
    found = set(InventoryItem.objects.filter(id__in=seen).values_list("id", flat=True))
    if found != seen:
        raise Invalid("کالای انبار انتخاب‌شده وجود ندارد.")
    return rows


@api_view(["PUT"])
def product_recipe(request, pk):
    """جایگزینی کامل دستور مصرف. items خالی = پاک‌کردن دستور (مجاز)."""
    try:
        Product.objects.only("id").get(pk=pk)
    except Product.DoesNotExist:
        raise NotFound("محصول پیدا نشد.")
    rows = _clean_items(request.data)  # همه‌ی اعتبارسنجی‌ها قبل از دست‌زدن به دیتابیس
    with transaction.atomic():
        product = Product.objects.select_for_update().get(pk=pk)
        recipe, _ = Recipe.objects.get_or_create(product=product)
        recipe.items.all().delete()
        if rows:
            RecipeItem.objects.bulk_create(
                [
                    RecipeItem(recipe=recipe, inventory_item_id=iid, quantity=q)
                    for iid, q in rows
                ]
            )
        else:
            recipe.delete()  # محصول بدون دستور = بدون ردیف Recipe (is_available هم همین را فرض می‌کند)
    items = (
        RecipeItem.objects.filter(recipe__product_id=pk)
        .select_related("inventory_item")
        .order_by("id")
    )
    return Response(recipe_dict(product, items))
