import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction
from django.db.models import ProtectedError
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.errors import Conflict, Invalid, NotFound

from . import services
from .models import InventoryItem, InventoryTransaction
from .serializers import purchase_dict, waste_dict

LIST_LIMIT = 500

MAX_STOCK = Decimal("1000000000")
MAX_UNIT_COST = Decimal("100000000")
_DIGITS = str.maketrans("يك٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "یک" + "0123456789" * 2)


def item_dict(i):
    """قالب JSON دقیقاً مطابق InventoryItem.fromJson در Flutter."""
    return {
        "id": i.id,
        "name": i.name,
        "unit": i.unit,
        "current_stock": float(i.current_stock),
        "min_stock": float(i.min_stock),
        "unit_cost": float(i.unit_cost),
        "description": i.description or None,
    }


def _norm(s):
    """یکسان‌سازی نام برای تشخیص تکراری: ی/ک عربی، ارقام، نیم‌فاصله، فاصله‌ی اضافه."""
    s = str(s).translate(_DIGITS).replace("\u200c", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def _clean_name(raw):
    name = str(raw or "").strip()
    if not name:
        raise Invalid("نام کالا را وارد کنید.")
    if len(name) > 60:
        raise Invalid("نام کالا حداکثر ۶۰ حرف باشد.")
    return name


def _check_unique(name, exclude_id=None):
    key = _norm(name)
    for pk, other in InventoryItem.objects.values_list("id", "name"):
        if pk != exclude_id and _norm(other) == key:
            raise Invalid("کالایی با این نام قبلاً ثبت شده است.")


def _dec(data, key, label, *, default, places, maximum):
    """اگر کلید نباشد default؛ وگرنه عدد Decimal نامنفی و محدود."""
    if key not in data or data[key] in (None, ""):
        return default
    raw = data[key]
    if isinstance(raw, bool):
        raise Invalid(f"{label} معتبر نیست.")
    try:
        v = Decimal(str(raw))
    except InvalidOperation:
        raise Invalid(f"{label} معتبر نیست.")
    if not v.is_finite() or v < 0 or v > maximum:
        raise Invalid(f"{label} معتبر نیست.")
    return v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _description(raw):
    text = str(raw or "").strip()
    if len(text) > 300:
        raise Invalid("توضیحات حداکثر ۳۰۰ حرف باشد.")
    return text


@api_view(["GET", "POST"])
def items(request):
    if request.method == "GET":
        return Response([item_dict(i) for i in InventoryItem.objects.order_by("id")])

    d = request.data
    name = _clean_name(d.get("name"))
    unit = d.get("unit")
    if unit not in InventoryItem.Unit.values:
        raise Invalid("واحد پایه نامعتبر است.")
    zero = Decimal(0)
    min_stock = _dec(
        d, "min_stock", "حداقل موجودی", default=zero, places=3, maximum=MAX_STOCK
    )
    unit_cost = _dec(
        d, "unit_cost", "قیمت خرید", default=zero, places=2, maximum=MAX_UNIT_COST
    )
    initial = _dec(
        d, "initial_stock", "موجودی اولیه", default=zero, places=3, maximum=MAX_STOCK
    )
    description = _description(d.get("description"))

    with transaction.atomic():
        _check_unique(name)
        item = InventoryItem.objects.create(
            name=name,
            unit=unit,
            current_stock=initial,
            min_stock=min_stock,
            unit_cost=unit_cost,
            description=description,
        )
        if initial > 0:  # هر تغییر موجودی باید در دفتر حرکت ثبت شود
            InventoryTransaction.objects.create(
                item=item,
                kind=InventoryTransaction.Kind.ADJUST,
                quantity=initial,
                note="موجودی اولیه",
            )
    return Response(item_dict(item), status=201)


@api_view(["GET", "PATCH", "PUT", "DELETE"])
def item_detail(request, pk):
    with transaction.atomic():
        try:
            item = InventoryItem.objects.select_for_update().get(pk=pk)
        except InventoryItem.DoesNotExist:
            raise NotFound("کالا پیدا نشد.")

        if request.method == "GET":
            return Response(item_dict(item))

        if request.method == "DELETE":
            if item.current_stock > 0:
                raise Conflict(
                    "این کالا موجودی دارد و قابل حذف نیست. "
                    "ابتدا موجودی را از طریق ثبت ضایعات صفر کنید."
                )
            if item.recipe_items.exists():
                raise Conflict(
                    "این کالا در دستور مصرف یک محصول استفاده شده؛ "
                    "ابتدا از دستور مصرف حذفش کنید."
                )
            try:
                item.delete()
            except ProtectedError:
                raise Conflict(
                    "این کالا سابقه‌ی خرید، ضایعات یا مصرف دارد و قابل حذف نیست."
                )
            return Response(status=204)

        # PATCH/PUT: unit و current_stock هرگز از این مسیر تغییر نمی‌کنند.
        d = request.data
        if "name" in d:
            name = _clean_name(d.get("name"))
            _check_unique(name, exclude_id=item.id)
            item.name = name
        item.min_stock = _dec(
            d,
            "min_stock",
            "حداقل موجودی",
            default=item.min_stock,
            places=3,
            maximum=MAX_STOCK,
        )
        item.unit_cost = _dec(
            d,
            "unit_cost",
            "قیمت خرید",
            default=item.unit_cost,
            places=2,
            maximum=MAX_UNIT_COST,
        )
        if "description" in d:
            item.description = _description(d.get("description"))
        item.save(update_fields=["name", "min_stock", "unit_cost", "description"])
        return Response(item_dict(item))


@api_view(["GET", "POST"])
def purchases(request):
    """خرید کالا: موجودی و آخرین قیمت خرید بالا می‌رود، پول از حساب انتخابی
    خارج و موجودی در دفتر حسابداری ثبت می‌شود (هزینه‌ی سود و زیان نیست)."""
    if request.method == "POST":
        t = services.create_purchase(request.data)
        return Response(purchase_dict(t), status=201)
    return Response([purchase_dict(t) for t in services.list_purchases()[:LIST_LIMIT]])


@api_view(["GET", "PATCH", "PUT", "DELETE"])
def purchase_detail(request, pk):
    """اصلاح/حذف یک خرید. اثر مالی قبلی جایگزین یا حذف می‌شود تا مبلغ قدیمی
    در گزارش‌ها باقی نماند (اصل ۱۱ پروژه)."""
    if request.method == "DELETE":
        services.delete_purchase(pk)
        return Response(status=204)
    if request.method in ("PATCH", "PUT"):
        t = services.update_purchase(pk, request.data)
        return Response(purchase_dict(t))
    t = services.list_purchases().filter(pk=pk).first()
    if t is None:
        raise NotFound("خرید پیدا نشد.")
    return Response(purchase_dict(t))


@api_view(["GET", "POST"])
def wastes(request):
    """ضایعات کالا: موجودی کم، زیان ضایعات در دفتر ثبت می‌شود؛ بیش از موجودی ۴۰۹."""
    if request.method == "POST":
        t = services.create_waste(request.data)
        return Response(waste_dict(t), status=201)
    return Response([waste_dict(t) for t in services.list_wastes()[:LIST_LIMIT]])


@api_view(["GET", "PATCH", "PUT", "DELETE"])
def waste_detail(request, pk):
    """اصلاح/حذف یک رکورد ضایعات؛ کالا به انبار برمی‌گردد و زیان از دفتر پاک می‌شود."""
    if request.method == "DELETE":
        services.delete_waste(pk)
        return Response(status=204)
    if request.method in ("PATCH", "PUT"):
        t = services.update_waste(pk, request.data)
        return Response(waste_dict(t))
    t = services.list_wastes().filter(pk=pk).first()
    if t is None:
        raise NotFound("ضایعات پیدا نشد.")
    return Response(waste_dict(t))
