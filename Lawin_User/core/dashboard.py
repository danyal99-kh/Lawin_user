"""داشبورد مدیریت.

«امروز» = روز تقویمی Asia/Tehran (TIME_ZONE پروژه) و «این ماه» = ماه شمسی جاری؛
هر دو از همان ابزارهای core.jalali که گزارش‌های حسابداری استفاده می‌کنند می‌آیند.

فروش = جمع سفارش‌های paid که زمان پرداختشان در بازه است (منطق پروژه در
accounting_api.build_report همین است). لغوشده‌ها نه در فروش و نه در شمارش
سفارش‌های امروز می‌آیند، ولی در recent_orders می‌مانند — چون فهرست سفارش‌های
پنل (GET /orders/) هم لغوشده‌ها را نشان می‌دهد.
"""

from datetime import timedelta

from django.db.models import F
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

from . import ledger
from .expense_api import expense_dict
from .jalali import start_of_day, start_of_jalali_month, to_jalali
from .models import CafeSettings, CashAccount, Expense
from .models import LedgerAccount

RECENT_ORDERS, RECENT_EXPENSES = 6, 5


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

    # همه‌ی ارقام مالی از دفتر حسابداری مرکزی خوانده می‌شوند (بدون محاسبه‌ی دوباره)
    # و «مانده‌ی کل»، «امروز» و «این ماه» همه در یک کوئری خوانده می‌شوند.
    _b = ledger.balances_multi(
        [("all", None, None), ("day", day_start, day_end),
         ("month", month_start, day_end)]
    )
    all_bal, spans, month = _b["all"], _b["day"], _b["month"]
    settings_row = CafeSettings.current()

    def pl(b):
        gross = b[LedgerAccount.REVENUE] - b[LedgerAccount.COGS]
        return {
            "revenue": b[LedgerAccount.REVENUE],
            "cogs": b[LedgerAccount.COGS],
            "waste": b[LedgerAccount.WASTE],
            "expenses": b[LedgerAccount.EXPENSE],
            "gross_profit": gross,
            "net_profit": gross - b[LedgerAccount.EXPENSE] - b[LedgerAccount.WASTE],
        }

    today_pl, month_pl = pl(spans), pl(month)

    today_orders = (
        Order.objects.filter(created_at__gte=day_start, created_at__lt=day_end)
        .exclude(status=OrderStatus.CANCELLED)
        .count()
    )

    low = sorted(
        InventoryItem.objects.filter(current_stock__lte=F("min_stock")), key=_ratio
    )

    # ترتیب از Meta.ordering مدل می‌آید (`-created_at`, سپس `-number`) تا وقتی چند
    # سفارش در یک ثانیه ثبت می‌شوند «جدیدترین» نامعین نشود.
    recent_orders = Order.objects.select_related("table").prefetch_related(
        "items", "payments"
    )[:RECENT_ORDERS]

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
            "today_sales": today_pl["revenue"],
            "month_sales": month_pl["revenue"],
            "today_expenses": today_pl["expenses"],
            "month_expenses": month_pl["expenses"],
            "today_cogs": today_pl["cogs"],
            "month_cogs": month_pl["cogs"],
            "today_waste": today_pl["waste"],
            "month_waste": month_pl["waste"],
            "today_profit": today_pl["net_profit"],
            "month_profit": month_pl["net_profit"],
            "today_gross_profit": today_pl["gross_profit"],
            "month_gross_profit": month_pl["gross_profit"],
            "inventory_value": all_bal[LedgerAccount.INVENTORY],
            "cash_balance": settings_row.opening_cash + all_bal[CashAccount.CASH],
            "bank_balance": settings_row.opening_bank + all_bal[CashAccount.BANK],
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
