"""منطق مهمان‌نوازی: ثبت، تغییر نام و لغو.

قانون‌های این ماژول:

1. مهمان یک سفارش واقعی است؛ موادش همان لحظه‌ی ساخت سفارش مصرف شده، پس
   مهمان‌کردن هیچ مصرف دوباره‌ای نمی‌سازد و لغو، موجودی را یک بار برمی‌گرداند.
2. هیچ ردیف Payment، هیچ صندوق/بانک و هیچ طلبی درگیر نمی‌شود؛ فقط سند
   هزینه‌ی مهمان‌نوازی در دفتر می‌آید (بدهکار هزینه، بستانکار درآمد فروش).
3. همه‌ی عملیات داخل تراکنش و با قفل ردیفی سفارش‌اند؛ تکرار یک درخواست بی‌اثر
   است (قید یکتایی سفارش + بررسی وضعیت فعال).
4. لغو، سند را با یک ورودی معکوس خنثا می‌کند و رکورد را حذف نمی‌کند.
"""

from django.db import transaction
from django.db.models import Count, Max, Sum
from django.utils import timezone

from core import events, ledger
from core.errors import Conflict, Invalid, NotFound
from orders.constants import OPEN_STATUSES, OrderStatus, PaymentMethod
from orders.models import Order
from tables import services as table_services
from tables.models import Table

from .models import Guest
from .serializers import guest_dict, group_dict

MAX_NAME = Guest.MAX_NAME


def _clean_name(raw):
    """نام مهمان: اجباری، تک‌خطی و در محدوده‌ی طول (پیش از برش نرمال‌سازی)."""
    if raw is None or not isinstance(raw, str):
        raise Invalid("نام مهمان را وارد کنید.")
    flat = " ".join(raw.split())
    if not flat:
        raise Invalid("نام مهمان را وارد کنید.")
    if len(flat) > MAX_NAME:
        raise Invalid(f"نام مهمان حداکثر {MAX_NAME} حرف باشد.")
    return flat


def _lock_order(order_id):
    try:
        return (
            Order.objects.select_for_update()
            .select_related("table", "session")
            .get(pk=order_id)
        )
    except (Order.DoesNotExist, ValueError, TypeError):
        raise NotFound("سفارش پیدا نشد.")


def _to_guest(order, name, user, now):
    """سفارش قفل‌شده‌ی باز و پرداخت‌نشده را مهمان می‌کند. باید داخل تراکنش باشد."""
    if order.payment_status != "unpaid":
        raise Conflict("فقط سفارش پرداخت‌نشده را می‌توان مهمان کرد.")
    if order.status not in OPEN_STATUSES:
        raise Conflict("این سفارش بسته شده و قابل مهمان‌کردن نیست.")
    if Guest.objects.filter(order=order).exists():
        raise Conflict("این سفارش قبلاً مهمان شده است.")

    previous = order.status
    guest = Guest.objects.create(
        name=name,
        order=order,
        amount=order.total,
        previous_status=previous,
        created_by=user if getattr(user, "pk", None) else None,
    )
    order.status = OrderStatus.PAID
    order.payment_status = "guest"
    order.payment_method = PaymentMethod.GUEST
    order.paid_at = now
    order.version += 1
    order.save()
    # هزینه‌ی مهمان‌نوازی: بدهکار هزینه، بستانکار درآمد. هیچ پولی جابه‌جا نمی‌شود.
    ledger.post_guest(order=order, amount=guest.amount, occurred_at=now)
    return guest


@transaction.atomic
def mark_guest(*, order_id, name, user=None):
    """یک سفارش را مهمان می‌کند. تکرار روی همان سفارش، همان ثبت را برمی‌گرداند."""
    clean = _clean_name(name)
    order = _lock_order(order_id)
    existing = Guest.objects.filter(order=order).first()
    if existing is not None and existing.status == Guest.Status.ACTIVE:
        return existing
    guest = _to_guest(order, clean, user, timezone.now())
    events.publish_admin(events.GUEST_CREATED, guest_dict(guest))
    return guest


@transaction.atomic
def mark_table_guest(*, table_id, name, user=None):
    """همه‌ی سفارش‌های باز میز را یک‌جا مهمان می‌کند (پایان حضور مهمان).

    معادل `orders.services.pay_table` برای روش مهمان: سفارش‌ها بسته و نشست
    در صورت خالی‌شدن بسته می‌شود، ولی هیچ پرداختی ثبت نمی‌شود.
    """
    clean = _clean_name(name)
    try:
        table_id = int(table_id)
    except (TypeError, ValueError):
        raise NotFound("میز پیدا نشد.")
    if not Table.objects.select_for_update().filter(pk=table_id).exists():
        raise NotFound("میز پیدا نشد.")
    orders = list(
        Order.objects.select_for_update()
        .select_related("table", "session")
        .filter(
            table_id=table_id,
            status__in=OPEN_STATUSES,
            session__exited_at__isnull=True,
        )
        .order_by("number")
    )
    if not orders:
        raise Conflict("سفارش بازی برای مهمان‌کردن وجود ندارد.")

    now = timezone.now()
    guests = [_to_guest(o, clean, user, now) for o in orders]
    closed = table_services.close_session_if_idle(orders[0].session, now)
    for g in guests:
        events.publish_admin(events.GUEST_CREATED, guest_dict(g))
    events.publish_admin(
        events.PAYMENT_COMPLETED,
        {
            "order_ids": [str(o.id) for o in orders],
            "table_id": table_id,
            "amount": sum(g.amount for g in guests),
            "method": PaymentMethod.GUEST,
            "session_closed": closed,
        },
    )
    return orders


@transaction.atomic
def rename_guest(*, guest_id, name, user=None):
    """تغییر نام مهمان. مبلغ و دفتر دست‌نخورده می‌ماند؛ فقط گروه‌بندی عوض می‌شود."""
    clean = _clean_name(name)
    try:
        guest = Guest.objects.select_for_update().select_related(
            "order", "order__table"
        ).get(pk=guest_id)
    except (Guest.DoesNotExist, ValueError, TypeError):
        raise NotFound("مهمان پیدا نشد.")
    if guest.status != Guest.Status.ACTIVE:
        raise Conflict("مهمان لغوشده قابل ویرایش نیست.")
    guest.name = clean
    guest.save(update_fields=["name"])
    events.publish_admin(events.GUEST_UPDATED, guest_dict(guest))
    return guest


@transaction.atomic
def cancel_guest(*, guest_id, user=None):
    """لغو مهمان‌نوازی: سند دفتر معکوس و موجودی سفارش برمی‌گردد.

    اثر کامل است (مثل برگشت سفارش): رکورد مهمان حذف نمی‌شود بلکه `cancelled`
    می‌شود، سفارش `cancelled/refunded` می‌شود و بهای تمام‌شده هم معکوس می‌گردد
    تا هیچ اثر مالی‌ای از پذیرایی رایگان باقی نماند. تکرار درخواست بی‌اثر است.
    """
    from orders import services as order_services

    try:
        guest = Guest.objects.select_for_update().select_related(
            "order", "order__table"
        ).get(pk=guest_id)
    except (Guest.DoesNotExist, ValueError, TypeError):
        raise NotFound("مهمان پیدا نشد.")
    if guest.status != Guest.Status.ACTIVE:
        return guest

    order = _lock_order(guest.order_id)
    if order.payment_status != "guest":
        raise Conflict("این سفارش دیگر مهمان نیست.")

    now = timezone.now()
    # ۱) خنثاسازی سند مهمان‌نوازی: بدهکار درآمد، بستانکار هزینه.
    ledger.reverse_guest(order=order, amount=guest.amount, occurred_at=now)
    # ۲) برگرداندن مواد مصرفی و معکوس بهای تمام‌شده (همان مسیر لغو سفارش).
    order_services._restore(order)
    # ۳) بستن سفارش و رکورد مهمان.
    previous = order.status
    order.status = OrderStatus.CANCELLED
    order.payment_status = "refunded"
    order.version += 1
    order.save()
    guest.status = Guest.Status.CANCELLED
    guest.cancelled_at = now
    guest.save(update_fields=["status", "cancelled_at"])

    order_services._publish_change(order, previous)
    events.publish_admin(events.GUEST_CANCELLED, guest_dict(guest))
    return guest


# ------------------------------------------------------------------- خواندن
def guest_groups(qs=None):
    """گروه مهمان‌های فعال بر اساس نام، با جمع مبلغ (یک کوئری)."""
    return (
        (qs or Guest.objects.all())
        .filter(status=Guest.Status.ACTIVE)
        .values("name")
        .annotate(
            total_amount=Sum("amount"),
            order_count=Count("id"),
            last_at=Max("created_at"),
        )
        .order_by("-total_amount", "name")
    )


def guest_total(qs=None):
    """جمع مبلغ مهمان‌نوازی فعال (همه یا زیرمجموعه‌ی داده‌شده)."""
    return (
        (qs or Guest.objects.all())
        .filter(status=Guest.Status.ACTIVE)
        .aggregate(s=Sum("amount"))["s"]
        or 0
    )


def guest_orders(name):
    """ثبت‌های فعال یک نام (نرمال‌شده)، برای صفحه‌ی جزئیات گروه."""
    clean = Guest.normalize(name)
    if not clean:
        raise Invalid("نام مهمان را مشخص کنید.")
    return (
        Guest.objects.filter(name=clean, status=Guest.Status.ACTIVE)
        .select_related("order", "order__table")
        .order_by("-created_at", "-id")
    )


def group_row(row):
    """ردیف خام annotation را به دیکشنری API تبدیل می‌کند (برای بازاستفاده)."""
    return group_dict(row)
