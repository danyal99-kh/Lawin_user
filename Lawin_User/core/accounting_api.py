"""همه‌ی گزارش‌های مالی از دفتر حسابداری مرکزی (core.ledger) خوانده می‌شوند.

هیچ گزارشی محاسبه‌ی مستقل و متناقض با دفتر ندارد: درآمد، بهای تمام‌شده،
ضایعات، هزینه، سود و زیان و گردش نقدینگی همه مانده‌ی حساب‌ها هستند. این
 تضمین می‌کند «مجموع پرداخت‌ها = درآمد» و «سود و زیان = مانده‌ی حساب‌ها» همیشه
دقیقاً برقرار باشد.
"""

from datetime import datetime, timedelta

from django.db.models import BigIntegerField, ExpressionWrapper, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from catalog.models import Product
from core import ledger
from core.errors import Invalid
from core.jalali import jalali_day, start_of_day
from core.models import CafeSettings, Expense, JournalKind, LedgerAccount
from core.security import HasSecurityTicket
from inventory.models import InventoryItem
from orders.constants import OrderStatus, PaymentMethod
from orders.models import Order, OrderItem
from rest_framework.permissions import IsAdminUser

# همه‌ی endpointهای مالی هم IsAdminUser هستند و هم بلیت امنیتی لازم دارند؛
# بازبینی «رمز امنیتی» فقط UI نیست و روی خود API هم اعمال می‌شود.
FINANCIAL_PERMISSIONS = [IsAdminUser, HasSecurityTicket]

LIMIT = 2000  # سقف هر نوع ردیف در دفتر (جدیدترین‌ها)
MAX_RANGE_DAYS = 366  # سقف بازه‌ی دلخواه گزارش

METHOD_LABELS = dict(PaymentMethod.choices)

EXPENSE_LABELS = {
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

# برچسب هر نوع رویداد دفتر (برای ردیف‌هایی که عنوان ندارند).
KIND_LABELS = {
    JournalKind.SALE: "فروش",
    JournalKind.COGS: "بهای تمام‌شده",
    JournalKind.COGS_REVERSE: "برگشت بهای تمام‌شده",
    JournalKind.PURCHASE: "خرید",
    JournalKind.WASTE: "ضایعات",
    JournalKind.EXPENSE: "هزینه",
    JournalKind.REFUND: "برگشت از فروش",
    JournalKind.CREDIT_PAYMENT: "تسویه نسیه",
    JournalKind.CREDIT_PAY_REV: "برگشت تسویه نسیه",
}

# نوع ردیف در API دفتر حسابداری: «درآمد» یعنی پولی که واقعاً باید بیاید/آمده،
# «هزینه» یعنی خروجی؛ `refund` برای برگشت فروش است (Flutter fallback آن هزینه است).
CREDIT_KINDS = {JournalKind.CREDIT_PAYMENT, JournalKind.CREDIT_PAY_REV}


def _iso(d):
    return d.isoformat()


# ---------------------------------------------------------------- دفتر حسابداری
@api_view(["GET"])
@permission_classes(FINANCIAL_PERMISSIONS)
def transactions(request):
    """GET /accounting/transactions/ — دفتر تراکنش‌های مالی از دفتر مرکزی.

    هر ردیف یک رویداد مالی واقعی است (فروش، خرید، ضایعات، هزینه، برگشت)، نه یک
    محاسبه‌ی دوباره از Order/Expense.
    """
    rows = []
    qs = ledger.JournalEntry.objects.prefetch_related("lines").order_by(
        "-occurred_at", "-id"
    )[:LIMIT]
    for e in qs:
        lines = list(e.lines.all())
        amount = sum(l.amount for l in lines if l.side == "debit")
        if e.kind == JournalKind.SALE:
            entry_type = "income"
        elif e.kind == JournalKind.CREDIT_PAYMENT:
            # وصول نسیه: پول واقعاً وارد صندوق/بانک شده پس درآمد نقدی است،
            # هرچند ورودی فروشش قبلاً هنگام ثبت نسیه خورده شده است.
            entry_type = "income"
        elif e.kind in (JournalKind.REFUND, JournalKind.CREDIT_PAY_REV):
            entry_type = "refund"
        elif e.kind in (
            JournalKind.PURCHASE, JournalKind.COGS, JournalKind.WASTE,
            JournalKind.EXPENSE, JournalKind.COGS_REVERSE,
        ):
            entry_type = "expense"
        else:
            entry_type = "expense"
        # JournalEntry فیلد `category` ندارد؛ عنوان از خودِ رویداد می‌آید و فقط
        # برای اطمینان به برچسب نوع رویداد برمی‌گردیم.
        title = e.title or KIND_LABELS.get(e.kind, "")
        subtitle = " • ".join(
            p
            for p in (e.detail or "", METHOD_LABELS.get(e.method, "") or "")
            if p
        )
        rows.append(
            {
                "id": f"{entry_type}-{e.id}",
                "type": entry_type,
                "kind": e.kind,
                "title": title,
                "subtitle": subtitle,
                "amount": amount,
                "date": _iso(e.occurred_at),
            }
        )
    rows.sort(key=lambda x: x["date"], reverse=True)
    return Response(rows)


# ---------------------------------------------------------------- گزارش
def _day_start(d):
    """مرز روز به وقت تهران، نیمه‌باز.

    عمداً از همان `core.jalali.start_of_day` استفاده می‌شود که داشبورد و خودِ
    سرویس‌های تاریخ‌محور استفاده می‌کنند؛ دو پیاده‌سازی جدا برای یک مفهوم
    («روز تهران») یعنی دو منبع حقیقت و دو راه برای باگ مرزی.
    """
    return start_of_day(d)


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


def _products_without_recipe():
    """محصولات فعالی که دستور مصرف ندارند ⇒ بهای تمام‌شده‌شان صفر است."""
    return [
        {"product_id": p.id, "product_name": p.name}
        for p in Product.objects.filter(is_active=True, recipe__isnull=True)
    ]


def build_report(start, end):
    """گزارش اصلی: همه‌ی ارقام از دفتر مرکزی."""
    orders = Order.objects.filter(
        status=OrderStatus.PAID, paid_at__gte=start, paid_at__lt=end
    )
    items = OrderItem.objects.filter(
        order__status=OrderStatus.PAID,
        order__paid_at__gte=start,
        order__paid_at__lt=end,
    )

    # --- ارقام مالی از دفتر (تنها منبع حقیقت) ---
    pl = ledger.profit_and_loss(start, end)
    settings_row = CafeSettings.current()
    cf = ledger.cash_flow(
        start, end, settings_row.opening_cash, settings_row.opening_bank
    )
    methods = ledger.payment_methods(start, end)
    paid_total = sum(m["amount"] for m in methods)

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
        Expense.objects.filter(date__gte=start, date__lt=end)
        .order_by()
        .values("category")
        .annotate(amount=Sum("amount"))
        .order_by("-amount")
    )

    # نمودار روند: درآمد و سود هر روز از دفتر
    days = (end.date() - start.date()).days
    first = timezone.localtime(start).date()
    points = []
    for i in range(days):
        d = first + timedelta(days=i)
        d0, d1 = _day_start(d), _day_start(d + timedelta(days=1))
        day_pl = ledger.profit_and_loss(d0, d1)
        points.append(
            {
                "date": d.isoformat(),
                "sales": day_pl["revenue"],
                "expenses": day_pl["expenses"] + day_pl["waste"] + day_pl["cogs"],
                "cogs": day_pl["cogs"],
                "waste": day_pl["waste"],
                "profit": day_pl["net_profit"],
            }
        )

    return {
        "start": _iso(start),
        "end": _iso(end),
        "total_sales": pl["revenue"],
        "total_cogs": pl["cogs"],
        "gross_profit": pl["gross_profit"],
        "total_expenses": pl["expenses"],
        "total_waste": pl["waste"],
        "net_profit": pl["net_profit"],
        "total_purchases": ledger.purchase_total(start, end),
        "inventory_value": ledger.inventory_value(),
        "payment_methods": [
            {
                "method": m["method"],
                "label": METHOD_LABELS.get(m["method"], m["method"]),
                "amount": m["amount"],
            }
            for m in methods
        ],
        "payments_total": paid_total,
        "cash_flow": cf,
        # نسیه: فروشِ اعتباری بازه، پولی که واقعاً از بدهکاران گرفته شده و
        # مانده‌ی کل طلبکاری (وضعیت لحظه‌ای، نه جریان بازه).
        "credit_sales": ledger.credit_sales(start, end),
        "credit_collections": ledger.credit_collections(start, end),
        "cash_received": cf["total_in"],
        "outstanding_receivables": ledger.balance(LedgerAccount.RECEIVABLE),
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
        "products_without_recipe": _products_without_recipe(),
    }


@api_view(["GET"])
@permission_classes(FINANCIAL_PERMISSIONS)
def reports(request):
    """GET /reports/?period=today|week|month|custom&start=YYYY-MM-DD&end=YYYY-MM-DD"""
    q = request.query_params
    start, end = resolve_range(q.get("period", "month"), q.get("start"), q.get("end"))
    return Response(build_report(start, end))


@api_view(["GET"])
@permission_classes(FINANCIAL_PERMISSIONS)
def verify(request):
    """GET /accounting/verify/ — بازرسی سلامت دفتر حسابداری.

    دفتر منبع حقیقت است، پس اگر روزی از واقعیت جدا شد باید *دیده* شود، نه اینکه
    گزارش‌ها آرام‌آرام غلط شوند. این endpoint بررسی می‌کند هر ورودی تراز است،
    درآمد دفتر با پرداخت‌ها می‌خواند، سفارش‌های تسویه‌شده ورودی فروش دارند و
    موجودی دفتر با کالای فیزیکی برابر است.
    """
    return Response(ledger.health())


# ---------------------------------------------------------------- گزارش انبار
@api_view(["GET"])
@permission_classes(FINANCIAL_PERMISSIONS)
def inventory_report(request):
    """GET /reports/inventory/ — ارزش و مقدار موجودی هر کالا."""
    q = request.query_params
    start, end = resolve_range(q.get("period", "month"), q.get("start"), q.get("end"))
    rows = []
    total_value = 0
    for item in InventoryItem.objects.order_by("name"):
        value = int(item.current_stock * item.unit_cost)
        total_value += value
        rows.append(
            {
                "id": item.id,
                "name": item.name,
                "unit": item.unit,
                "current_stock": float(item.current_stock),
                "min_stock": float(item.min_stock),
                "unit_cost": float(item.unit_cost),
                "value": value,
                "is_low": item.current_stock <= item.min_stock,
            }
        )
    return Response(
        {
            "start": _iso(start),
            "end": _iso(end),
            "items": rows,
            "total_value": total_value,
            "ledger_inventory_value": ledger.inventory_value(),
        }
    )


# ---------------------------------------------------------------- گزارش روش پرداخت
@api_view(["GET"])
@permission_classes(FINANCIAL_PERMISSIONS)
def payment_methods_report(request):
    """GET /reports/payment-methods/ — تفکیک درآمد بر اساس روش پرداخت."""
    q = request.query_params
    start, end = resolve_range(q.get("period", "month"), q.get("start"), q.get("end"))
    methods = ledger.payment_methods(start, end)
    total = sum(m["amount"] for m in methods)
    return Response(
        {
            "start": _iso(start),
            "end": _iso(end),
            "methods": [
                {
                    "method": m["method"],
                    "label": METHOD_LABELS.get(m["method"], m["method"]),
                    "amount": m["amount"],
                    "percent": round(m["amount"] * 100 / total, 1) if total else 0,
                }
                for m in methods
            ],
            "total": total,
        }
    )


# ---------------------------------------------------------------- گزارش ضایعات
@api_view(["GET"])
@permission_classes(FINANCIAL_PERMISSIONS)
def waste_report(request):
    """GET /reports/waste/ — ضایعات به تفکیک کالا و دلیل."""
    q = request.query_params
    start, end = resolve_range(q.get("period", "month"), q.get("start"), q.get("end"))
    from inventory.models import InventoryTransaction

    txs = InventoryTransaction.objects.filter(
        kind=InventoryTransaction.Kind.WASTE,
        created_at__gte=start,
        created_at__lt=end,
    ).select_related("item")
    total = sum(int(abs(t.quantity) * (t.unit_cost or 0)) for t in txs)
    by_item = {}
    for t in txs:
        key = t.item_name_snapshot or t.item.name
        agg = by_item.setdefault(key, {"item_name": key, "quantity": 0.0, "value": 0})
        agg["quantity"] += abs(float(t.quantity))
        agg["value"] += int(abs(t.quantity) * (t.unit_cost or 0))
    return Response(
        {
            "start": _iso(start),
            "end": _iso(end),
            "total_value": total,
            "ledger_waste": ledger.balance("waste", start, end),
            "by_item": sorted(by_item.values(), key=lambda x: -x["value"]),
        }
    )


# ---------------------------------------------------------------- گزارش خرید
@api_view(["GET"])
@permission_classes(FINANCIAL_PERMISSIONS)
def purchase_report(request):
    """GET /reports/purchases/ — خریدها به تفکیک کالا."""
    q = request.query_params
    start, end = resolve_range(q.get("period", "month"), q.get("start"), q.get("end"))
    from inventory.models import InventoryTransaction

    txs = InventoryTransaction.objects.filter(
        kind=InventoryTransaction.Kind.PURCHASE,
        created_at__gte=start,
        created_at__lt=end,
    ).select_related("item")
    total = sum(int(abs(t.quantity) * (t.unit_cost or 0)) for t in txs)
    by_item = {}
    for t in txs:
        key = t.item_name_snapshot or t.item.name
        agg = by_item.setdefault(key, {"item_name": key, "quantity": 0.0, "value": 0})
        agg["quantity"] += abs(float(t.quantity))
        agg["value"] += int(abs(t.quantity) * (t.unit_cost or 0))
    return Response(
        {
            "start": _iso(start),
            "end": _iso(end),
            "total_value": total,
            "ledger_purchases": ledger.purchase_total(start, end),
            "by_item": sorted(by_item.values(), key=lambda x: -x["value"]),
        }
    )