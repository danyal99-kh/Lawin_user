from datetime import datetime, timedelta

from django.db.models import BigIntegerField, ExpressionWrapper, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.errors import Invalid
from core.jalali import jalali_day
from core.models import Expense
from orders.constants import OrderStatus, PaymentMethod
from orders.models import Order, OrderItem

LIMIT = 2000  # سقف هر نوع ردیف در دفتر (جدیدترین‌ها)
MAX_RANGE_DAYS = 366  # سقف بازه‌ی دلخواه گزارش

EXPENSE_LABELS = {
    "raw_materials": "خرید مواد اولیه",
    "salary": "حقوق",
    "rent": "اجاره",
    "water": "آب",
    "electricity": "برق",
    "gas": "گاز",
    "internet": "اینترنت",
    "repairs": "تعمیرات",
    "equipment": "تجهیزات",
    "advertising": "تبلیغات",
    "transport": "حمل‌ونقل",
    "supplies": "مواد مصرفی",
    "other": "سایر هزینه‌ها",
}
METHOD_LABELS = dict(PaymentMethod.choices)


def _iso(d):
    return d.isoformat()


# ---------------------------------------------------------------- دفتر حسابداری
@api_view(["GET"])
def transactions(request):
    """GET /accounting/transactions/ — درآمد (سفارش پرداخت‌شده) + هزینه، جدیدترین اول.
    قالب دقیقاً مطابق AccountingEntry.fromJson."""
    entries = []
    paid = (
        Order.objects.filter(status=OrderStatus.PAID, paid_at__isnull=False)
        .select_related("table")
        .order_by("-paid_at")[:LIMIT]
    )
    for o in paid:
        parts = [f"میز {o.table.number}"]
        if o.payment_method:
            parts.append(METHOD_LABELS.get(o.payment_method, o.payment_method))
        entries.append(
            {
                "id": f"income-{o.id}",
                "type": "income",
                "title": f"سفارش {o.number}",
                "subtitle": " • ".join(parts),
                "amount": o.total,
                "date": _iso(o.paid_at),
            }
        )
    for e in Expense.objects.order_by("-date")[:LIMIT]:
        entries.append(
            {
                "id": f"expense-{e.id}",
                "type": "expense",
                "title": e.title,
                "subtitle": EXPENSE_LABELS.get(e.category, e.category),
                "amount": e.amount,
                "date": _iso(e.date),
            }
        )
    entries.sort(key=lambda x: x["date"], reverse=True)
    return Response(entries)


# ---------------------------------------------------------------- گزارش
def _day_start(d):
    return timezone.make_aware(datetime(d.year, d.month, d.day))  # منطقه‌ی زمانی Tehran


def resolve_range(period, start_raw=None, end_raw=None):
    """(start, end) با end نیمه‌باز. هفته از شنبه، ماه بر اساس تقویم شمسی."""
    today = timezone.localdate()
    tomorrow = today + timedelta(days=1)
    if period == "today":
        return _day_start(today), _day_start(tomorrow)
    if period == "week":
        return _day_start(
            today - timedelta(days=(today.weekday() + 2) % 7)
        ), _day_start(tomorrow)
    if period == "month":
        return _day_start(today - timedelta(days=jalali_day(today) - 1)), _day_start(
            tomorrow
        )
    if period == "custom":
        s, e = parse_date(start_raw or ""), parse_date(end_raw or "")
        if s is None or e is None:
            raise Invalid("بازه‌ی زمانی نامعتبر است.")
        if s > e:
            raise Invalid("تاریخ شروع نباید بعد از تاریخ پایان باشد.")
        if (e - s).days >= MAX_RANGE_DAYS:
            raise Invalid("بازه‌ی گزارش حداکثر یک سال می‌تواند باشد.")
        return _day_start(s), _day_start(
            e + timedelta(days=1)
        )  # هر دو روز شامل می‌شوند
    raise Invalid("نوع بازه‌ی گزارش نامعتبر است.")


def build_report(start, end):
    orders = Order.objects.filter(
        status=OrderStatus.PAID, paid_at__gte=start, paid_at__lt=end
    )
    expenses = Expense.objects.filter(date__gte=start, date__lt=end)
    items = OrderItem.objects.filter(
        order__status=OrderStatus.PAID,
        order__paid_at__gte=start,
        order__paid_at__lt=end,
    )

    revenue = ExpressionWrapper(
        F("unit_price") * F("quantity"), output_field=BigIntegerField()
    )
    top = (
        items.order_by()
        .values("product_id", "product_name")
        .annotate(revenue=Sum(revenue), quantity=Sum("quantity"))
        .order_by("-revenue")[:10]
    )
    by_cat = (
        expenses.order_by()
        .values("category")
        .annotate(amount=Sum("amount"))
        .order_by("-amount")
    )

    sales_by_day = {
        r["d"]: r["s"]
        for r in orders.order_by()
        .annotate(d=TruncDate("paid_at"))
        .values("d")
        .annotate(s=Sum("total"))
    }
    exp_by_day = {
        r["d"]: r["s"]
        for r in expenses.order_by()
        .annotate(d=TruncDate("date"))
        .values("d")
        .annotate(s=Sum("amount"))
    }
    days = (end.date() - start.date()).days
    first = timezone.localtime(start).date()
    points = []
    for i in range(days):
        d = first + timedelta(days=i)
        points.append(
            {
                "date": d.isoformat(),
                "sales": sales_by_day.get(d, 0),
                "expenses": exp_by_day.get(d, 0),
            }
        )

    return {
        "start": _iso(start),
        "end": _iso(end),
        "total_sales": orders.aggregate(s=Sum("total"))["s"] or 0,
        "total_expenses": expenses.aggregate(s=Sum("amount"))["s"] or 0,
        "order_count": orders.count(),
        "items_sold_count": items.aggregate(s=Sum("quantity"))["s"] or 0,
        "top_products": [
            {
                "product_id": r["product_id"] or 0,
                "product_name": r["product_name"],
                "quantity": r["quantity"],
                "revenue": r["revenue"],
            }
            for r in top
        ],
        "expenses_by_category": [
            {"category": r["category"], "amount": r["amount"]} for r in by_cat
        ],
        "daily_points": points,
    }


@api_view(["GET"])
def reports(request):
    """GET /reports/?period=today|week|month|custom&start=YYYY-MM-DD&end=YYYY-MM-DD"""
    q = request.query_params
    start, end = resolve_range(q.get("period", "month"), q.get("start"), q.get("end"))
    return Response(build_report(start, end))
