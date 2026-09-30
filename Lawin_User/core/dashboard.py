"""خلاصه‌ی داشبورد ادمین. خروجی دقیقاً منطبق با DashboardSummary.fromJson در Flutter."""

from django.db.models import F, Sum
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.models import Expense
from inventory.models import InventoryItem
from orders.constants import OrderStatus
from orders.models import Order
from orders.serializers import order_dict
from tables.models import Table
from tables.serializers import table_dict


def _jalali_month_start(now_aware):
    """اول ماه شمسی جاری، به‌صورت DateTime آگاه از Timezone.
    اگر jdatetime نصب نباشد، به اول ماه میلادی برمی‌گردد (فقط fallback موقت)."""
    try:
        import jdatetime

        j = jdatetime.datetime.fromgregorian(datetime=now_aware)
        start_naive = j.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).togregorian()
        return (
            timezone.make_aware(start_naive, timezone.get_current_timezone())
            if timezone.is_naive(start_naive)
            else start_naive
        )
    except ImportError:
        return now_aware.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _inventory_item_dict(i):
    return {
        "id": i.id,
        "name": i.name,
        "unit": i.unit,
        "current_stock": float(i.current_stock),
        "min_stock": float(i.min_stock),
        "unit_cost": float(i.unit_cost),
        "description": i.description or None,
    }


def _expense_dict(e):
    return {
        "id": e.id,
        "title": e.title,
        "amount": e.amount,
        "category": e.category,
        "date": e.date.isoformat(),
        "note": e.note or None,
    }


@api_view(["GET"])
def dashboard_summary(request):
    now = timezone.localtime()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = _jalali_month_start(now)

    def sales(since):
        return (
            Order.objects.filter(status=OrderStatus.PAID, paid_at__gte=since).aggregate(
                s=Sum("total")
            )["s"]
            or 0
        )

    def expenses(since):
        return (
            Expense.objects.filter(date__gte=since).aggregate(s=Sum("amount"))["s"] or 0
        )

    today_order_count = (
        Order.objects.filter(created_at__gte=day_start)
        .exclude(status=OrderStatus.CANCELLED)
        .count()
    )

    low_stock = InventoryItem.objects.filter(
        current_stock__lte=F("min_stock")
    ).order_by("current_stock")

    recent_orders = (
        Order.objects.select_related("table")
        .prefetch_related("items")
        .order_by("-created_at")[:6]
    )

    recent_expenses = Expense.objects.order_by("-date")[:5]

    return Response(
        {
            "today_sales": sales(day_start),
            "month_sales": sales(month_start),
            "today_expenses": expenses(day_start),
            "month_expenses": expenses(month_start),
            "today_order_count": today_order_count,
            "tables": [table_dict(t) for t in Table.objects.all()],
            "low_stock_items": [_inventory_item_dict(i) for i in low_stock],
            "recent_orders": [order_dict(o) for o in recent_orders],
            "recent_expenses": [_expense_dict(e) for e in recent_expenses],
        }
    )
