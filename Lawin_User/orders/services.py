"""تمام منطق حساس سفارش/موجودی/پرداخت. هیچ عددی از Client پذیرفته نمی‌شود:
قیمت از Product، مبلغ کل و کسر موجودی در همین‌جا و داخل یک تراکنش محاسبه می‌شود."""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from catalog.models import Product
from core import events
from core.errors import Conflict, InactiveProduct, Invalid, NotFound, OutOfStock
from core.models import Counter
from inventory.models import InventoryItem, InventoryTransaction, RecipeItem
from tables import services as table_services
from tables.models import Table

from .constants import NEXT_STATUS, OPEN_STATUSES, OrderSource, OrderStatus, PaymentMethod
from .models import Order, OrderItem, Payment
from .serializers import order_dict

MAX_QTY, MAX_LINES = 20, 30


def _clean_lines(raw):
    if not isinstance(raw, list) or not raw:
        raise Invalid("حداقل یک آیتم را اضافه کنید.")
    if len(raw) > MAX_LINES:
        raise Invalid("تعداد آیتم‌های سفارش بیش از حد مجاز است.")
    lines = []
    for r in raw:
        try:
            pid, qty = int(r["product_id"]), int(r["quantity"])
        except (TypeError, ValueError, KeyError):
            raise Invalid("اطلاعات سبد سفارش نامعتبر است.")
        if not 1 <= qty <= MAX_QTY:
            raise Invalid("تعداد هر آیتم باید بین ۱ تا ۲۰ باشد.")
        lines.append({"product_id": pid, "quantity": qty, "note": str(r.get("note") or "").strip()[:120]})
    return lines  # هر قیمتی که Client فرستاده باشد نادیده گرفته می‌شود


def _plan_inventory(products, lines):
    """نیاز انبار را از Recipe حساب می‌کند، ردیف‌ها را قفل می‌کند و کمبود را رد می‌کند."""
    qty = defaultdict(int)
    for l in lines:
        qty[l["product_id"]] += l["quantity"]
    need, users = defaultdict(Decimal), defaultdict(set)
    for ri in RecipeItem.objects.filter(recipe__product_id__in=list(qty)).select_related("recipe"):
        pid = ri.recipe.product_id
        need[ri.inventory_item_id] += ri.quantity * qty[pid]
        users[ri.inventory_item_id].add(products[pid].name)
    if not need:
        return {}, {}
    # قفل به ترتیب id: جلوگیری از deadlock بین سفارش‌های همزمان
    items = {i.id: i for i in InventoryItem.objects.select_for_update().filter(id__in=list(need)).order_by("id")}
    short = sorted({n for iid, q in need.items() if items[iid].current_stock < q for n in users[iid]})
    if short:
        raise OutOfStock("، ".join(f"«{n}»" for n in short) + " در حال حاضر موجود نیست.")
    return need, items


def _consume(order, need, items):
    for iid, q in need.items():
        it = items[iid]
        it.current_stock -= q
        it.save(update_fields=["current_stock"])
        InventoryTransaction.objects.create(item=it, kind=InventoryTransaction.Kind.CONSUME,
                                            quantity=-q, order=order, note=f"سفارش {order.number}")


def _restore(order):
    sums = defaultdict(Decimal)
    for t in order.inventory_transactions.filter(kind=InventoryTransaction.Kind.CONSUME):
        sums[t.item_id] += -t.quantity
    for it in InventoryItem.objects.select_for_update().filter(id__in=list(sums)).order_by("id"):
        it.current_stock += sums[it.id]
        it.save(update_fields=["current_stock"])
        InventoryTransaction.objects.create(item=it, kind=InventoryTransaction.Kind.RESTORE,
                                            quantity=sums[it.id], order=order, note=f"لغو سفارش {order.number}")


@transaction.atomic
def create_order(*, table_id, items, source=OrderSource.CUSTOMER, customer_key="", customer_note=""):
    lines = _clean_lines(items)
    try:
        table_id = int(table_id)
    except (TypeError, ValueError):
        raise NotFound("میز پیدا نشد.")
    if not Table.objects.filter(pk=table_id).exists():
        raise NotFound("میز پیدا نشد.")

    products = {p.id: p for p in Product.objects.select_related("category").filter(
        id__in={l["product_id"] for l in lines})}
    if len(products) != len({l["product_id"] for l in lines}):
        raise NotFound("یکی از محصولات پیدا نشد.")
    for p in products.values():
        if not p.is_active or not p.category.is_active:
            raise InactiveProduct(f"«{p.name}» فعلاً موجود نیست.")

    need, locked = _plan_inventory(products, lines)          # ۵–۸: بررسی + قفل موجودی
    session, table = table_services.open_session(table_id)   # قانون نشست
    order = Order.objects.create(
        number=Counter.next("order"), source=source, table=table, session=session,
        customer_key=customer_key, customer_note=(customer_note or "").strip()[:200])
    OrderItem.objects.bulk_create([
        OrderItem(order=order, product=products[l["product_id"]], product_name=products[l["product_id"]].name,
                  unit_price=products[l["product_id"]].price, quantity=l["quantity"], note=l["note"])
        for l in lines])
    order.total = sum(i.unit_price * i.quantity for i in order.items.all())
    order.save(update_fields=["total"])
    _consume(order, need, locked)                            # ۱۱: کسر امن موجودی
    events.publish_admin(events.ORDER_CREATED, {"order": order_dict(order)})
    return order


def _lock(order_id):
    try:
        return Order.objects.select_for_update().select_related("table", "session").get(pk=order_id)
    except (Order.DoesNotExist, ValueError):
        raise NotFound("سفارش پیدا نشد.")


def _publish_change(order, previous):
    payload = {"order": order_dict(order), "previous_status": previous}
    events.publish_admin(events.ORDER_STATUS_CHANGED, payload)
    events.publish_customer(order.customer_key, events.ORDER_STATUS_CHANGED, payload)


@transaction.atomic
def change_status(order_id, new_status):
    order = _lock(order_id)
    if new_status == order.status:
        return order
    if not order.is_open:
        raise Conflict("این سفارش بسته شده و قابل تغییر نیست.")
    if new_status == OrderStatus.CANCELLED:
        _restore(order)
    elif NEXT_STATUS.get(order.status) != new_status:
        raise Conflict("این تغییر وضعیت مجاز نیست. پرداخت از بخش پرداخت انجام می‌شود.")
    previous, order.status = order.status, new_status
    order.version += 1
    order.save()
    if new_status == OrderStatus.CANCELLED:
        table_services.close_session_if_idle(order.session)
    _publish_change(order, previous)
    return order


def _check_method(method):
    if method not in PaymentMethod.values:
        raise Invalid("روش پرداخت نامعتبر است.")


def _settle(orders, method, user):
    now, session = timezone.now(), orders[0].session
    for o in orders:
        previous = o.status
        o.status, o.payment_status, o.payment_method = OrderStatus.PAID, "paid", method
        o.paid_at, o.version = now, o.version + 1
        o.save()
        Payment.objects.create(order=o, method=method, amount=o.total,
                               created_by=user if getattr(user, "pk", None) else None)
        _publish_change(o, previous)
    closed = table_services.close_session_if_idle(session, now)
    events.publish_admin(events.PAYMENT_COMPLETED, {
        "order_ids": [str(o.id) for o in orders], "table_id": session.table_id,
        "amount": sum(o.total for o in orders), "method": method, "session_closed": closed})
    return orders


@transaction.atomic
def pay_order(order_id, method, user=None):
    _check_method(method)
    order = _lock(order_id)
    if not order.is_open:
        raise Conflict("این سفارش قبلاً بسته شده است.")
    return _settle([order], method, user)[0]


@transaction.atomic
def pay_table(table_id, method, user=None):
    """پرداخت همه‌ی سفارش‌های باز میز (پایان حضور مشتری)."""
    _check_method(method)
    if not Table.objects.select_for_update().filter(pk=table_id).exists():
        raise NotFound("میز پیدا نشد.")
    orders = list(Order.objects.select_for_update().select_related("table", "session").filter(
        table_id=table_id, status__in=OPEN_STATUSES, session__exited_at__isnull=True).order_by("number"))
    if not orders:
        raise Conflict("سفارش بازی برای پرداخت وجود ندارد.")
    return _settle(orders, method, user)


@transaction.atomic
def mark_bar_printed(order_id):
    order = _lock(order_id)
    order.bar_printed_at = timezone.now()
    order.version += 1
    order.save()
    return order
