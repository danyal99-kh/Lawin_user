from datetime import timedelta

from django.db.models import F, Sum
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from inventory.models import InventoryItem
from orders.constants import OrderStatus
from orders.models import Order
from orders.serializers import order_dict
from tables.models import Table
from tables.serializers import table_dict

from .jalali import start_of_day, start_of_jalali_month
from .models import Expense

RECENT_ORDERS, RECENT_EXPENSES = 6, 5


def expense_dict(e):
    """مطابق Expense.fromJson. (در مرحله ۴ هم از همین استفاده کن.)"""
    return {
        "id": e.id,
        "title": e.title,
        "amount": e.amount,
        "category": e.category,
        "date": e.date.isoformat(),
        "note": e.note or None,
    }


def inventory_item_dict(i):
    """مطابق InventoryItem.fromJson؛ Decimal → float."""
    return {
        "id": i.id,
        "name": i.name,
        "unit": i.unit,
        "current_stock": float(i.current_stock),
        "min_stock": float(i.min_stock),
        "unit_cost": float(i.unit_cost),
        "description": i.description or None,
    }


def _sales(start, end):
    """درآمد = جمع سفارش‌های پرداخت‌شده‌ای که زمان پرداختشان در بازه است."""
    return (
        Order.objects.filter(
            status=OrderStatus.PAID, paid_at__gte=start, paid_at__lt=end
        ).aggregate(s=Sum("total"))["s"]
        or 0
    )


def _expenses(start, end):
    return (
        Expense.objects.filter(date__gte=start, date__lt=end).aggregate(
            s=Sum("amount")
        )["s"]
        or 0
    )


def _ratio(i):
    return float(i.current_stock / i.min_stock) if i.min_stock else 0.0


@api_view(["GET"])
def summary(request):
    now = timezone.now()
    day_start = start_of_day(now)
    day_end = day_start + timedelta(days=1)  # روز تقویمی؛ برای این کار DST مهم نیست
    month_start = start_of_jalali_month(now)

    today_orders = (
        Order.objects.filter(created_at__gte=day_start, created_at__lt=day_end)
        .exclude(status=OrderStatus.CANCELLED)
        .count()
    )

    low = sorted(
        InventoryItem.objects.filter(current_stock__lte=F("min_stock")), key=_ratio
    )

    recent_orders = (
        Order.objects.select_related("table")
        .prefetch_related("items")
        .order_by("-created_at")[:RECENT_ORDERS]
    )

    return Response(
        {
            "today_sales": _sales(day_start, day_end),
            "month_sales": _sales(month_start, day_end),
            "today_expenses": _expenses(day_start, day_end),
            "month_expenses": _expenses(month_start, day_end),
            "today_order_count": today_orders,
            "tables": [table_dict(t) for t in Table.objects.all()],
            "low_stock_items": [inventory_item_dict(i) for i in low],
            "recent_orders": [order_dict(o) for o in recent_orders],
            "recent_expenses": [
                expense_dict(e)
                for e in Expense.objects.order_by("-date")[:RECENT_EXPENSES]
            ],
        }
    )
