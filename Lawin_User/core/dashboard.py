"""داشبورد مدیریت.

«امروز» = روز تقویمی Asia/Tehran (TIME_ZONE پروژه) و «این ماه» = ماه شمسی جاری؛
هر دو از همان ابزارهای core.jalali که گزارش‌های حسابداری استفاده می‌کنند می‌آیند.

فروش = جمع سفارش‌های paid که زمان پرداختشان در بازه است (منطق پروژه در
accounting_api.build_report همین است). لغوشده‌ها نه در فروش و نه در شمارش
سفارش‌های امروز می‌آیند، ولی در recent_orders می‌مانند — چون فهرست سفارش‌های
پنل (GET /orders/) هم لغوشده‌ها را نشان می‌دهد.
"""

from datetime import timedelta

from django.db.models import F, Sum
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from inventory.models import InventoryItem
from inventory.serializers import item_dict
from orders.constants import OrderStatus
from orders.models import Order
from orders.serializers import order_dict
from tables.models import Table
from tables.serializers import table_dict

from .expense_api import expense_dict
from .jalali import start_of_day, start_of_jalali_month, to_jalali
from .models import Expense

RECENT_ORDERS, RECENT_EXPENSES = 6, 5


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
    day_end = start_of_day(day_start + timedelta(days=1))  # نیمه‌باز: ۰۰:۰۰ روز بعد تهران
    month_start = start_of_jalali_month(now)
    jy, jm, jd = to_jalali(
        day_start.year, day_start.month, day_start.day
    )  # ماه شمسی جاری تهران

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
            "period": {
                "calendar": "jalali",
                "year": jy,
                "month": jm,
                "day": jd,
                "day_start": day_start.isoformat(),
                "day_end": day_end.isoformat(),
                "month_start": month_start.isoformat(),
            },
            "today_sales": _sales(day_start, day_end),
            "month_sales": _sales(month_start, day_end),
            "today_expenses": _expenses(day_start, day_end),
            "month_expenses": _expenses(month_start, day_end),
            "today_order_count": today_orders,
            "tables": [table_dict(t) for t in Table.objects.all()],
            "low_stock_items": [item_dict(i) for i in low],
            "recent_orders": [order_dict(o) for o in recent_orders],
            "recent_expenses": [
                expense_dict(e)
                for e in Expense.objects.order_by("-date")[:RECENT_EXPENSES]
            ],
        }
    )
