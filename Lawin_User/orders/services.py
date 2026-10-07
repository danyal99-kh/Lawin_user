"""تمام منطق حساس سفارش/موجودی/پرداخت. هیچ عددی از Client پذیرفته نمی‌شود:
قیمت از Product، مبلغ کل و کسر موجودی در همین‌جا و داخل یک تراکنش محاسبه می‌شود."""
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from catalog.models import Product
from core import events, ledger
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
    """کسر موجودی طبق Recipe و ثبت بهای تمام‌شده در دفتر.

    `unit_cost` در همان لحظه snapshot می‌شود تا تغییر بعدی قیمت کالا، بهای
    تمام‌شده‌ی فروش‌های گذشته را تغییر ندهد (اصل ۹ پروژه).
    """
    total_value = 0
    for iid, q in need.items():
        it = items[iid]
        it.current_stock -= q
        it.save(update_fields=["current_stock"])
        value = int(q * (it.unit_cost or 0))
        total_value += value
        InventoryTransaction.objects.create(
            item=it,
            kind=InventoryTransaction.Kind.CONSUME,
            quantity=-q,
            unit_cost=it.unit_cost,  # snapshot بهای تمام‌شده
            order=order,
            note=f"سفارش {order.number}",
        )
    # بدهکار COGS / بستانکار انبار — فقط وقتی محصولی Recipe دارد
    ledger.post_cogs(order=order, amount=total_value, occurred_at=order.created_at)


def _cogs_value(order):
    """ارزش مواد مصرفی یک سفارش از روی snapshot ثبت‌شده در دفتر حرکت."""
    return int(
        sum(
            abs(t.quantity) * (t.unit_cost or 0)
            for t in order.inventory_transactions.filter(
                kind=InventoryTransaction.Kind.CONSUME
            )
        )
    )


def _restore(order):
    """برگرداندن مواد مصرفی سفارش و معکوس کردن بهای تمام‌شده در دفتر."""
    sums = defaultdict(Decimal)
    for t in order.inventory_transactions.filter(kind=InventoryTransaction.Kind.CONSUME):
        sums[t.item_id] += -t.quantity
    for it in InventoryItem.objects.select_for_update().filter(id__in=list(sums)).order_by("id"):
        it.current_stock += sums[it.id]
        it.save(update_fields=["current_stock"])
        InventoryTransaction.objects.create(
            item=it,
            kind=InventoryTransaction.Kind.RESTORE,
            quantity=sums[it.id],
            order=order,
            note=f"لغو سفارش {order.number}",
        )
    # معکوس COGS: بدهکار انبار، بستانکار COGS (به‌جای حذف ردیف قبلی، اثر مالی
    # خنثا می‌شود تا تاریخچه‌ی دفتر کامل بماند).
    ledger.reverse_cogs(order=order, amount=_cogs_value(order))


@transaction.atomic
def create_order(*, table_id, items, source=OrderSource.CUSTOMER, customer_key="",
                 customer_note="", idempotency_key=None):
    """سفارش را می‌سازد، قیمت را از Product می‌گیرد، موجودی را کم و بهای تمام‌شده
    را در دفتر ثبت می‌کند.

    با `idempotency_key` پردازش دوباره‌ی یک درخواست، سفارش دوم نمی‌سازد؛ همان
    سفارش قبلی برگردانده می‌شود تا درآمد و مصرف مواد دو بار ثبت نشوند (اصل ۱۳).
    """
    if idempotency_key:
        existing = Order.objects.filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            return existing

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
        customer_key=customer_key, customer_note=(customer_note or "").strip()[:200],
        idempotency_key=idempotency_key or None)
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


def _split_lines(raw, total):
    """پرداخت یک‌روشی یا چندروشی را به فهرست (method, amount) نرمال می‌کند.

    مجموع مبالغ باید **دقیقاً** برابر مبلغ سفارش باشد؛ اختلاف پذیرفته نیست.
    """
    if raw is None:
        raise Invalid("روش پرداخت را انتخاب کنید.")
    if isinstance(raw, str):
        raw = [{"method": raw}]
    elif isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise Invalid("روش پرداخت را انتخاب کنید.")
    if len(raw) > len(PaymentMethod.values):
        raise Invalid("تعداد روش‌های پرداخت معتبر نیست.")

    out = []
    for r in raw:
        method, amount = r.get("method"), r.get("amount")
        _check_method(method)
        if amount is None:
            amount = total
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            raise Invalid("مبلغ پرداخت معتبر نیست.")
        if amount <= 0:
            raise Invalid("مبلغ پرداخت باید بیشتر از صفر باشد.")
        out.append((method, amount))

    got = sum(a for _, a in out)
    if got != total:
        raise Invalid(
            f"جمع پرداخت‌ها ({got:,}) باید دقیقاً برابر مبلغ سفارش ({total:,}) باشد."
        )
    return out


def _settle(orders, payments, user):
    """ثبت پرداخت و فروش در دفتر. `payments` فهرست (method, amount) به تفکیک سفارش."""
    now, session = timezone.now(), orders[0].session
    total_paid = 0
    for o in orders:
        previous = o.status
        lines = payments[o.pk]
        o.status, o.payment_status = OrderStatus.PAID, "paid"
        # روش اصلی: اولین روش پرداخت؛ گزارش تفکیکی از Paymentها می‌خواند
        o.payment_method = lines[0][0]
        o.paid_at, o.version = now, o.version + 1
        o.save()
        for method, amount in lines:
            payment = Payment.objects.create(
                order=o, method=method, amount=amount,
                created_by=user if getattr(user, "pk", None) else None,
            )
            # هر پرداخت یک ورودی فروش مستقل در دفتر؛ کلید یکتایی روی خود Payment
            # است تا دو پرداخت هم‌روشِ یک سفارش هرکدام جدا ثبت شوند.
            ledger.post_sale(order=o, method=method, amount=amount,
                             occurred_at=now, source_key=f"payment:{payment.pk}")
            total_paid += amount
        _publish_change(o, previous)
    closed = table_services.close_session_if_idle(session, now)
    events.publish_admin(events.PAYMENT_COMPLETED, {
        "order_ids": [str(o.id) for o in orders], "table_id": session.table_id,
        "amount": total_paid, "method": orders[0].payment_method, "session_closed": closed})
    return orders


@transaction.atomic
def pay_order(order_id, method=None, user=None, payments=None):
    """پرداخت یک سفارش. `payments` برای پرداخت چندروشی است."""
    order = _lock(order_id)
    if not order.is_open:
        raise Conflict("این سفارش قبلاً بسته شده است.")
    lines = _split_lines(payments if payments is not None else method, order.total)
    return _settle([order], {order.pk: lines}, user)[0]


@transaction.atomic
def pay_table(table_id, method=None, user=None, payments=None):
    """پرداخت همه‌ی سفارش‌های باز میز (پایان حضور مشتری).

    در حالت چندروشی، `payments` بین سفارش‌های باز به ترتیب شماره تخصیص می‌یابد و
    هر سفارش دقیقاً به اندازه‌ی مبلغ خودش تسویه می‌شود.
    """
    _check_method(method) if payments is None else None
    if not Table.objects.select_for_update().filter(pk=table_id).exists():
        raise NotFound("میز پیدا نشد.")
    orders = list(Order.objects.select_for_update().select_related("table", "session").filter(
        table_id=table_id, status__in=OPEN_STATUSES, session__exited_at__isnull=True).order_by("number"))
    if not orders:
        raise Conflict("سفارش بازی برای پرداخت وجود ندارد.")
    grand = sum(o.total for o in orders)

    if payments is None:
        alloc = {o.pk: [(method, o.total)] for o in orders}
    else:
        flat = _split_lines(payments, grand)
        alloc, i = {}, 0
        for o in orders:  # تخصیص به ترتیب مبلغ سفارش؛ هرگز بیش از مبلغ سفارش
            part = []
            left = o.total
            while left > 0:
                method, amount = flat[i]
                take = min(amount, left)
                part.append((method, take))
                flat[i] = (method, amount - take)
                left -= take
                if flat[i][1] == 0:
                    i += 1
            alloc[o.pk] = part
    return _settle(orders, alloc, user)


@transaction.atomic
def refund_order(order_id, user=None):
    """برگشت کامل یک سفارش پرداخت‌شده: درآمد، پول، موجودی و بهای تمام‌شده خنثا می‌شود.

    طبق اصل ۱۰ پروژه هیچ اثر مالی قبلی نباید باقی بماند. پول دقیقاً از همان
    حسابی که پرداخت شده بود برگردانده می‌شود.
    """
    order = _lock(order_id)
    if order.status != OrderStatus.PAID or order.payment_status != "paid":
        raise Conflict("فقط سفارش پرداخت‌شده را می‌توان برگرداند.")
    payments = list(order.payments.all())
    if not payments:
        raise Conflict("برای این سفارش پرداختی ثبت نشده است.")

    # موجودی باید اجازه‌ی بازگشت بدهد؛ وگرنه پول برمی‌گردد ولی انبار نه، که
    # حسابداری را ناسازگار می‌کند.
    restore = defaultdict(Decimal)
    for t in order.inventory_transactions.filter(
        kind=InventoryTransaction.Kind.CONSUME
    ):
        restore[t.item_id] += -t.quantity
    locked = {
        i.id: i for i in InventoryItem.objects.select_for_update()
        .filter(id__in=list(restore)).order_by("id")
    }
    short = sorted(
        {locked[iid].name for iid, q in restore.items()
         if iid in locked and locked[iid].current_stock < q}
    )
    if short:
        raise Conflict(
            "بازگشت کالا به انبار ممکن نیست؛ موجودی «"
            + "، ".join(short)
            + "» کمتر از مقدار مصرف‌شده است. ابتدا موجودی را اصلاح کنید."
        )

    previous = order.status
    now = timezone.now()
    for p in payments:
        # درآمد برگشتی + بازگشت پول از همان حسابی که پرداخت شده بود.
        # کلید یکتایی روی همان Payment است تا refund چندروشی همه ثبت شود.
        ledger.post_refund(order=order, method=p.method, amount=p.amount,
                           occurred_at=now, source_key=f"payment:{p.pk}")
    for iid, q in restore.items():
        it = locked[iid]
        it.current_stock += q
        it.save(update_fields=["current_stock"])
        InventoryTransaction.objects.create(
            item=it, kind=InventoryTransaction.Kind.RESTORE, quantity=q,
            order=order, note=f"برگشت سفارش {order.number}",
        )
    ledger.reverse_cogs(
        order=order, amount=_cogs_value(order), occurred_at=now,
        item_detail="برگشت بهای تمام‌شده",
    )

    order.status, order.payment_status = OrderStatus.CANCELLED, "refunded"
    order.version += 1
    order.save(update_fields=["status", "payment_status", "version", "updated_at"])
    _publish_change(order, previous)
    return order


@transaction.atomic
def mark_bar_printed(order_id):
    order = _lock(order_id)
    order.bar_printed_at = timezone.now()
    order.version += 1
    order.save()
    return order
