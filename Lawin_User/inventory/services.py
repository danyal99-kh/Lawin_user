"""منطق کسب‌وکار انبار. همان قوانینی که MockInventoryRepository سمت Flutter دارد."""

from decimal import Decimal, InvalidOperation

from django.db import transaction

from core.errors import Conflict, Invalid, NotFound, OutOfStock

from catalog.models import Product

from .models import InventoryItem, InventoryTransaction, Recipe, RecipeItem

MAX_NAME_LEN = 60
MAX_UNIT_COST = Decimal("100000000")
MAX_STOCK = Decimal("1000000000")  # سقف موجودی؛ هم‌راستا با MAX_STOCK در api.py
VALID_UNITS = {c[0] for c in InventoryItem.Unit.choices}


def _decimal(value, field_label):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise Invalid(f"{field_label} معتبر نیست.")


def _validate_common(raw_name, min_stock, unit_cost, exclude_id=None):
    name = (raw_name or "").strip()
    if not name:
        raise Invalid("نام کالا را وارد کنید.")
    if len(name) > MAX_NAME_LEN:
        raise Invalid(f"نام کالا حداکثر {MAX_NAME_LEN} حرف باشد.")
    if min_stock < 0:
        raise Invalid("حداقل موجودی نمی‌تواند منفی باشد.")
    if unit_cost < 0 or unit_cost > MAX_UNIT_COST:
        raise Invalid("قیمت خرید واردشده معتبر نیست.")
    qs = InventoryItem.objects.filter(name__iexact=name)
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    if qs.exists():
        raise Invalid("کالایی با این نام قبلاً ثبت شده است.")
    return name


def list_items():
    return InventoryItem.objects.order_by("name")


@transaction.atomic
def create_item(data):
    unit = data.get("unit")
    if unit not in VALID_UNITS:
        raise Invalid("واحد پایه نامعتبر است.")
    min_stock = _decimal(data.get("min_stock", 0), "حداقل موجودی")
    unit_cost = _decimal(data.get("unit_cost", 0), "قیمت خرید")
    initial_stock = _decimal(data.get("initial_stock", 0), "موجودی اولیه")
    if initial_stock < 0:
        raise Invalid("موجودی اولیه معتبر نیست.")
    name = _validate_common(data.get("name"), min_stock, unit_cost)
    return InventoryItem.objects.create(
        name=name,
        unit=unit,
        current_stock=initial_stock,
        min_stock=min_stock,
        unit_cost=unit_cost,
        description=(data.get("description") or "").strip()[:300],
    )


@transaction.atomic
def update_item(pk, data):
    try:
        item = InventoryItem.objects.select_for_update().get(pk=pk)
    except InventoryItem.DoesNotExist:
        raise NotFound("کالا پیدا نشد.")
    min_stock = _decimal(data.get("min_stock", item.min_stock), "حداقل موجودی")
    unit_cost = _decimal(data.get("unit_cost", item.unit_cost), "قیمت خرید")
    name = _validate_common(
        data.get("name", item.name), min_stock, unit_cost, exclude_id=item.pk
    )
    item.name = name
    item.min_stock = min_stock
    item.unit_cost = unit_cost
    item.description = (data.get("description", item.description) or "").strip()[:300]
    # واحد پایه و موجودی فعلی بعد از ایجاد از این مسیر تغییر نمی‌کند
    item.save(update_fields=["name", "min_stock", "unit_cost", "description"])
    return item


@transaction.atomic
def delete_item(pk):
    try:
        item = InventoryItem.objects.select_for_update().get(pk=pk)
    except InventoryItem.DoesNotExist:
        raise NotFound("کالا پیدا نشد.")
    if item.current_stock > 0:
        raise Conflict(
            "این کالا موجودی دارد و قابل حذف نیست. ابتدا موجودی را از طریق ثبت ضایعات صفر کنید."
        )
    item.delete()


def list_purchases():
    return (
        InventoryTransaction.objects.filter(kind=InventoryTransaction.Kind.PURCHASE)
        .select_related("item")
        .order_by("-created_at")
    )


@transaction.atomic
def create_purchase(data):
    item_id = data.get("item_id")
    try:
        item = InventoryItem.objects.select_for_update().get(pk=item_id)
    except (InventoryItem.DoesNotExist, ValueError, TypeError):
        raise Invalid("کالای انتخاب‌شده وجود ندارد.")

    quantity = _decimal(data.get("quantity"), "مقدار خرید")
    if quantity <= 0:
        raise Invalid("مقدار خرید باید بیشتر از صفر باشد.")
    if quantity > MAX_STOCK:
        raise Invalid("مقدار خرید معتبر نیست.")
    unit_cost = _decimal(data.get("unit_cost", 0), "قیمت خرید")
    if unit_cost < 0 or unit_cost > MAX_UNIT_COST:
        raise Invalid("قیمت خرید واردشده معتبر نیست.")
    if item.current_stock + quantity > MAX_STOCK:
        raise Invalid("موجودی پس از این خرید معتبر نیست.")

    item.current_stock += quantity
    item.unit_cost = unit_cost  # آخرین قیمت خرید مبناست (میانگین نمی‌گیریم)
    item.save(update_fields=["current_stock", "unit_cost"])

    note = str(data.get("note") or "").strip()[:200]
    return InventoryTransaction.objects.create(
        item=item,
        item_name_snapshot=item.name,
        kind=InventoryTransaction.Kind.PURCHASE,
        quantity=quantity,
        unit_cost=unit_cost,
        note=note,
    )


VALID_WASTE_REASONS = {c[0] for c in InventoryTransaction.WasteReason.choices}


def list_wastes():
    return (
        InventoryTransaction.objects.filter(kind=InventoryTransaction.Kind.WASTE)
        .select_related("item")
        .order_by("-created_at")
    )


@transaction.atomic
def create_waste(data):
    item_id = data.get("item_id")
    try:
        item = InventoryItem.objects.select_for_update().get(pk=item_id)
    except (InventoryItem.DoesNotExist, ValueError, TypeError):
        raise Invalid("کالای انتخاب‌شده وجود ندارد.")

    quantity = _decimal(data.get("quantity"), "مقدار ضایعات")
    if quantity <= 0:
        raise Invalid("مقدار ضایعات باید بیشتر از صفر باشد.")
    if quantity > item.current_stock:
        raise OutOfStock(f"موجودی «{item.name}» کافی نیست.")

    reason = data.get("reason")
    if not reason:
        raise Invalid("دلیل ضایعات را انتخاب کنید.")
    if reason not in VALID_WASTE_REASONS:
        raise Invalid("دلیل ضایعات نامعتبر است.")

    item.current_stock -= quantity
    item.save(update_fields=["current_stock"])

    note = str(data.get("note") or "").strip()[:200]
    return InventoryTransaction.objects.create(
        item=item,
        item_name_snapshot=item.name,
        kind=InventoryTransaction.Kind.WASTE,
        quantity=-quantity,
        unit_cost=item.unit_cost,
        reason=reason,
        note=note,
    )


def list_recipes():
    """(product, items) برای همه‌ی محصولات؛ محصول بدون دستور مصرف items=[] دارد."""
    products = Product.objects.order_by("sort_order", "id").prefetch_related(
        "recipe__items__inventory_item"
    )
    result = []
    for p in products:
        try:
            items = list(p.recipe.items.select_related("inventory_item").all())
        except Recipe.DoesNotExist:
            items = []
        result.append((p, items))
    return result


def _clean_recipe_items(raw_items):
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        raise Invalid("اطلاعات دستور مصرف نامعتبر است.")
    cleaned = []
    seen = set()
    for raw in raw_items:
        try:
            iid = int(raw["inventory_item_id"])
            qty = _decimal(raw["quantity"], "مقدار مصرفی")
        except (TypeError, ValueError, KeyError):
            raise Invalid("اطلاعات دستور مصرف نامعتبر است.")
        if qty <= 0:
            raise Invalid("مقدار مصرفی باید بیشتر از صفر باشد.")
        if iid in seen:
            raise Invalid("هر کالای انبار فقط یک‌بار قابل افزودن است.")
        seen.add(iid)
        cleaned.append((iid, qty))
    if cleaned:
        ids = {iid for iid, _ in cleaned}
        existing = set(
            InventoryItem.objects.filter(id__in=ids).values_list("id", flat=True)
        )
        if ids - existing:
            raise Invalid("کالای انبار انتخاب‌شده وجود ندارد.")
    return cleaned


@transaction.atomic
def save_recipe(product_id, raw_items):
    try:
        product = Product.objects.get(pk=product_id)
    except (Product.DoesNotExist, ValueError, TypeError):
        raise NotFound("محصول پیدا نشد.")

    cleaned = _clean_recipe_items(raw_items)

    recipe, _ = Recipe.objects.get_or_create(product=product)
    recipe.items.all().delete()
    RecipeItem.objects.bulk_create(
        [
            RecipeItem(recipe=recipe, inventory_item_id=iid, quantity=qty)
            for iid, qty in cleaned
        ]
    )
    items = list(recipe.items.select_related("inventory_item").all())
    return product, items
