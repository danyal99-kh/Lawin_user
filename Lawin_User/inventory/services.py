"""منطق کسب‌وکار انبار. همان قوانینی که MockInventoryRepository سمت Flutter دارد."""

from decimal import Decimal, InvalidOperation

from django.db import transaction

from core import ledger
from core.errors import Conflict, Invalid, NotFound, OutOfStock
from core.models import CashAccount, JournalKind, LedgerAccount, Side

from catalog.models import Product

from .models import InventoryItem, InventoryTransaction, Recipe, RecipeItem

MAX_NAME_LEN = 60
MAX_UNIT_COST = Decimal("100000000")
MAX_STOCK = Decimal("1000000000")  # سقف موجودی؛ هم‌راستا با MAX_STOCK در api.py
VALID_UNITS = {c[0] for c in InventoryItem.Unit.choices}
VALID_CASH_ACCOUNTS = {c[0] for c in CashAccount.choices}


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
    item = InventoryItem.objects.create(
        name=name,
        unit=unit,
        current_stock=initial_stock,
        min_stock=min_stock,
        unit_cost=unit_cost,
        description=(data.get("description") or "").strip()[:300],
    )
    if initial_stock and unit_cost:
        # موجودی اولیه یعنی کالایی که *قبل* از فعال شدن سیستم در انبار بوده؛
        # ارزشش باید در دفتر هم بیاید وگرنه موجودی دفتر با کالای فیزیکی نمی‌خواند و
        # بهای تمام‌شده‌ی فروش‌های بعدی بی‌پشتوانه می‌شود. تاریخش به `created_at`
        # کالا می‌خورد نه امروز، تا در گزارشِ روزِ ثبت کالا دیده شود.
        opening = (
            int(initial_stock * unit_cost) if isinstance(unit_cost, Decimal)
            and isinstance(initial_stock, Decimal)
            else 0
        )
        if opening:
            ledger.post_opening_stock(item=item, amount=opening)
    return item


def _revalue_existing_stock(item, previous_cost, new_cost):
    """اگر قیمت کالا عوض شده و کالا از قبل موجودی داشته، ارزش آن هم عوض می‌شود.

    این مسیر خیلی راحت از قلم می‌افتاد: کالایی که *قبل* از ثبت قیمتش موجود بوده
    (مثلاً کالای اولیه‌ی که `unit_cost=0` ساخته شده) با اولین خرید قیمت می‌گیرد،
    ولی آن موجودی قدیمی هیچ سندی در دفتر ندارد. نتیجه: ارزش موجودی در گزارش
    اصلی با گزارش انبار نمی‌خواند (مثال واقعی: ۱۲۵٬۰۰۰ در برابر ۳۷۵٬۰۰۰).

    سطر انبار با `quantity=0` ثبت می‌شود: نه موجودی می‌لغزد، نه پول جابه‌جا
    می‌شود (پول این کالا را موقع خرید داده‌ایم) و هر تغییر قیمت یک ورودی
    جدا با شناسه‌ی یکتای خودش می‌سازد، پس قابل حسابرسی است.
    """
    if new_cost == previous_cost or not item.current_stock:
        return None
    delta = int(item.current_stock * new_cost) - int(
        item.current_stock * (previous_cost or 0)
    )
    if not delta:
        return None
    t = InventoryTransaction.objects.create(
        item=item,
        item_name_snapshot=item.name,
        kind=InventoryTransaction.Kind.REVALUATION,
        quantity=Decimal("0"),
        unit_cost=new_cost,
        note=f"تغییر قیمت از {previous_cost} به {new_cost}",
    )
    ledger.post(
        replace=True,
        kind=JournalKind.REVALUATION,
        source_type="revaluation",
        source_id=t.pk,
        occurred_at=t.created_at,
        lines=[
            (
                LedgerAccount.INVENTORY,
                Side.DEBIT if delta > 0 else Side.CREDIT,
                abs(delta),
            )
        ],
        title=f"تجدید ارزش {item.name}",
        detail=f"{item.current_stock} × {new_cost}",
    )
    return t


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
    previous_cost = item.unit_cost
    item.unit_cost = unit_cost
    item.description = (data.get("description", item.description) or "").strip()[:300]
    # واحد پایه و موجودی فعلی بعد از ایجاد از این مسیر تغییر نمی‌کند
    item.save(update_fields=["name", "min_stock", "unit_cost", "description"])

    # تغییر قیمتِ کالای موجود ارزش انبار را عوض می‌کند و باید در دفتر هم بیاید؛
    # وگرنه ارزش گزارش‌شده با موجودی واقعی کالا نمی‌خواند و بازرسی دفتر آن را
    # اختلاف اعلام می‌کند.
    _revalue_existing_stock(item, previous_cost, unit_cost)
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


def _purchase_amount(t):
    """ارزش خطی خرید به تومانِ صحیح."""
    return int(abs(t.quantity) * (t.unit_cost or 0))


def _purchase_lines(t, account):
    return [
        (LedgerAccount.INVENTORY, Side.DEBIT, _purchase_amount(t)),
        (account, Side.CREDIT, _purchase_amount(t)),
    ]


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

    account = data.get("account") or CashAccount.CASH
    if account not in VALID_CASH_ACCOUNTS:
        raise Invalid("حساب پرداخت نامعتبر است.")

    # اگر کالا از قبل موجودی داشته و این اولین قیمتش است (مثلاً کالای اولیه‌ی
    # `unit_cost=0`)، آن موجودی قدیمی هم باید با این قیمت در دفتر بنشیند.
    # ترتیب مهم است: *قبل* از افزودن مقدار خرید، تا فقط موجودیِ قبلی تجدید ارزش
    # شود و مقدار جدید با خودِ سطر خرید بنشیند (دوباره‌شماری نشود).
    _revalue_existing_stock(item, item.unit_cost, unit_cost)

    item.current_stock += quantity
    item.unit_cost = unit_cost  # آخرین قیمت خرید مبناست (میانگین نمی‌گیریم)
    item.save(update_fields=["current_stock", "unit_cost"])

    note = str(data.get("note") or "").strip()[:200]
    t = InventoryTransaction.objects.create(
        item=item,
        item_name_snapshot=item.name,
        kind=InventoryTransaction.Kind.PURCHASE,
        quantity=quantity,
        unit_cost=unit_cost,
        account=account,
        note=note,
    )
    # خرید در دفتر: موجودی انبار بالا می‌رود و پول خارج می‌شود؛ تا وقتی مصرف
    # نشده هزینه‌ی سود و زیان نیست (اصل ۱ پروژه).
    ledger.post(kind=JournalKind.PURCHASE, source_type="purchase", source_id=t.pk,
                occurred_at=t.created_at, lines=_purchase_lines(t, account),
                title=f"خرید {item.name}")
    return t


@transaction.atomic
def update_purchase(pk, data):
    """اصلاح خرید: مقدار/قیمت/حساب. اثر مالی قبلی جایگزین می‌شود، نه انباشته."""
    try:
        t = InventoryTransaction.objects.select_for_update().get(
            pk=pk, kind=InventoryTransaction.Kind.PURCHASE
        )
    except (InventoryTransaction.DoesNotExist, ValueError, TypeError):
        raise NotFound("خرید پیدا نشد.")
    item = InventoryItem.objects.select_for_update().get(pk=t.item_id)

    quantity = _decimal(data.get("quantity", t.quantity), "مقدار خرید")
    if quantity <= 0 or quantity > MAX_STOCK:
        raise Invalid("مقدار خرید معتبر نیست.")
    unit_cost = _decimal(data.get("unit_cost", t.unit_cost or 0), "قیمت خرید")
    if unit_cost < 0 or unit_cost > MAX_UNIT_COST:
        raise Invalid("قیمت خرید واردشده معتبر نیست.")
    account = data.get("account") or t.account or CashAccount.CASH
    if account not in VALID_CASH_ACCOUNTS:
        raise Invalid("حساب پرداخت نامعتبر است.")

    delta = quantity - t.quantity
    if item.current_stock + delta < 0:
        raise OutOfStock(
            f"کاهش مقدار خرید موجودی «{item.name}» را منفی می‌کند."
        )
    if item.current_stock + delta > MAX_STOCK:
        raise Invalid("موجودی پس از این اصلاح معتبر نیست.")

    t.quantity, t.unit_cost, t.account = quantity, unit_cost, account
    t.note = str(data.get("note", t.note) or "").strip()[:200]
    t.save(update_fields=["quantity", "unit_cost", "account", "note"])
    item.current_stock += delta
    item.save(update_fields=["current_stock"])
    ledger.post(kind=JournalKind.PURCHASE, source_type="purchase", source_id=t.pk,
                occurred_at=t.created_at, lines=_purchase_lines(t, account),
                title=f"خرید {t.item_name_snapshot or item.name}", replace=True)
    return t


@transaction.atomic
def delete_purchase(pk):
    """حذف خرید: موجودی و ورودی دفتر با هم برمی‌گردند."""
    try:
        t = InventoryTransaction.objects.select_for_update().get(
            pk=pk, kind=InventoryTransaction.Kind.PURCHASE
        )
    except (InventoryTransaction.DoesNotExist, ValueError, TypeError):
        raise NotFound("خرید پیدا نشد.")
    item = InventoryItem.objects.select_for_update().get(pk=t.item_id)
    if item.current_stock - abs(t.quantity) < 0:
        raise Conflict(
            "این خرید بخشی از موجودی فعلی است. ابتدا با ثبت ضایعات یا اصلاح "
            "مقدار خرید، موجودی را به صفر برسانید."
        )
    item.current_stock -= abs(t.quantity)
    item.save(update_fields=["current_stock"])
    ledger.remove(kind=JournalKind.PURCHASE, source_type="purchase", source_id=t.pk)
    t.delete()


VALID_WASTE_REASONS = {c[0] for c in InventoryTransaction.WasteReason.choices}


def list_wastes():
    return (
        InventoryTransaction.objects.filter(kind=InventoryTransaction.Kind.WASTE)
        .select_related("item")
        .order_by("-created_at")
    )


def _waste_amount(t):
    return int(abs(t.quantity) * (t.unit_cost or 0))


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
    t = InventoryTransaction.objects.create(
        item=item,
        item_name_snapshot=item.name,
        kind=InventoryTransaction.Kind.WASTE,
        quantity=-quantity,
        # snapshot ارزش: بهای ضایعات همان لحظه در دفتر ثبت می‌شود
        unit_cost=item.unit_cost,
        reason=reason,
        note=note,
    )
    # ضایعات در دفتر: موجودی کم و زیان ضایعات ثبت می‌شود؛ اثر نقدی ندارد
    # چون پولش هنگام خرید پرداخت شده است (اصل ۳ پروژه).
    ledger.post(kind=JournalKind.WASTE, source_type="waste", source_id=t.pk,
                occurred_at=t.created_at,
                lines=[(LedgerAccount.WASTE, Side.DEBIT, _waste_amount(t)),
                       (LedgerAccount.INVENTORY, Side.CREDIT, _waste_amount(t))],
                title=f"ضایعات {item.name}")
    return t


@transaction.atomic
def update_waste(pk, data):
    """اصلاح ضایعات: مقدار/دلیل/یادداشت؛ زیان ضایعات در دفتر جایگزین می‌شود."""
    try:
        t = InventoryTransaction.objects.select_for_update().get(
            pk=pk, kind=InventoryTransaction.Kind.WASTE
        )
    except (InventoryTransaction.DoesNotExist, ValueError, TypeError):
        raise NotFound("ضایعات پیدا نشد.")
    item = InventoryItem.objects.select_for_update().get(pk=t.item_id)

    quantity = _decimal(data.get("quantity", abs(t.quantity)), "مقدار ضایعات")
    if quantity <= 0:
        raise Invalid("مقدار ضایعات باید بیشتر از صفر باشد.")
    reason = data.get("reason") or t.reason
    if reason not in VALID_WASTE_REASONS:
        raise Invalid("دلیل ضایعات نامعتبر است.")

    delta = abs(t.quantity) - quantity  # مثبت یعنی مقداری به انبار برمی‌گردد
    if item.current_stock + delta < 0:
        raise OutOfStock(f"موجودی «{item.name}» کافی نیست.")

    t.quantity = -quantity
    t.reason = reason
    t.note = str(data.get("note", t.note) or "").strip()[:200]
    t.save(update_fields=["quantity", "reason", "note"])
    item.current_stock += delta
    item.save(update_fields=["current_stock"])
    ledger.post(kind=JournalKind.WASTE, source_type="waste", source_id=t.pk,
                occurred_at=t.created_at,
                lines=[(LedgerAccount.WASTE, Side.DEBIT, _waste_amount(t)),
                       (LedgerAccount.INVENTORY, Side.CREDIT, _waste_amount(t))],
                title=f"ضایعات {t.item_name_snapshot or item.name}", replace=True)
    return t


@transaction.atomic
def delete_waste(pk):
    """حذف رکورد ضایعات: کالا به انبار برمی‌گردد و زیان آن از دفتر پاک می‌شود."""
    try:
        t = InventoryTransaction.objects.select_for_update().get(
            pk=pk, kind=InventoryTransaction.Kind.WASTE
        )
    except (InventoryTransaction.DoesNotExist, ValueError, TypeError):
        raise NotFound("ضایعات پیدا نشد.")
    item = InventoryItem.objects.select_for_update().get(pk=t.item_id)
    if item.current_stock + abs(t.quantity) > MAX_STOCK:
        raise Invalid("موجودی پس از بازگرداندن ضایعات معتبر نیست.")
    item.current_stock += abs(t.quantity)
    item.save(update_fields=["current_stock"])
    ledger.remove(kind=JournalKind.WASTE, source_type="waste", source_id=t.pk)
    t.delete()


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
