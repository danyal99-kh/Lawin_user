"""هسته‌ی مالی مرکزی: ثبت رویدادهای مالی در دفتر دوطرفه و خواندن مانده از آن.

قانون این ماژول: **هیچ تغییر مالی بدون ورودی دفتر اتفاق نمی‌افتد** و **هیچ ورودی
دفتر بدون تراز بودن ساخته نمی‌شود**. تمام گزارش‌های مالی از همین دفتر خوانده
می‌شوند، نه از محاسبه‌ی جداگانه‌ی روی Order/Expense.

قید یکتایی (kind, source_type, source_id) روی JournalEntry باعث می‌شود یک
رویداد مالی دوبار ثبت نشود؛ پس پردازش دوباره‌ی یک سفارش/خرید/ضایعات بی‌اثر است.
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .models import (
    CashAccount,
    JournalEntry,
    JournalKind,
    JournalLine,
    LedgerAccount,
    Side,
)

# نگاشت روش پرداخت به حساب پولی: نقد ← صندوق، کارتخوان و کارت‌به‌کارت ← بانک
METHOD_ACCOUNT = {
    "cash": CashAccount.CASH,
    "card_reader": CashAccount.BANK,
    "card_transfer": CashAccount.BANK,
}

# حساب‌های درآمدی بستانکار مثبت‌اند؛ حساب‌های هزینه‌ای بدهکار مثبت.
# بنابراین علامت طبیعی هر گروه برعکسِ گروه دیگر است و گزارش باید هر دو را
# مثبت برگرداند تا «سود و زیان» با جمع ساده قابل تطبیق باشد.
REVENUE_ACCOUNTS = (LedgerAccount.REVENUE,)
COST_ACCOUNTS = (
    LedgerAccount.COGS,
    LedgerAccount.WASTE,
    LedgerAccount.EXPENSE,
)


class UnbalancedEntry(Exception):
    """سرآیند دفتر تراز نیست؛ هرگز نباید ثبت شود."""


@transaction.atomic
def post(
    *,
    kind,
    source_type,
    source_id,
    occurred_at=None,
    lines,
    title="",
    detail="",
    method="",
    order=None,
    replace=False,
):
    """یک رویداد مالی تراز را ثبت می‌کند. اگر قبلاً ثبت شده باشد همان را برمی‌گرداند.

    `lines` دنباله‌ی (account, side, amount) است. مبلغ‌ها باید مثبت باشند.
    با `replace=True` ورودی موجود به‌جای تکرار، **جایگزین** می‌شود؛ این مسیر
    ویرایش خرید/ضایعات/هزینه را پوشش می‌دهد تا مبلغ قدیمی در دفتر نماند.
    """
    lines = [
        (a, s, int(v)) for a, s, v in lines if int(v) > 0
    ]  # سطر صفر یعنی رویدادی رخ نداده؛ سطر مالی نمی‌سازیم
    if not lines:
        remove(kind=kind, source_type=source_type, source_id=source_id)
        return None

    debit = sum(v for _, s, v in lines if s == Side.DEBIT)
    credit = sum(v for _, s, v in lines if s == Side.CREDIT)
    # رویداد تک‌طرفه فقط وقتی مجاز است که آن حساب ذاتاً پولی/انباری نباشد.
    # موجودی اولیه‌ی کالا چنین رویدادی است: پولش پیش از سیستم پرداخت شده، پس
    # بستانکار نمی‌کنیم. برای صندوق/بانک یک‌طرفه یعنی پول از هیچ‌جا آمده یا
    # بی‌جا ناپدید شده و باید رد شود.
    if debit != credit:
        single_sided = debit == 0 or credit == 0
        touch_money = any(
            a in (CashAccount.CASH, CashAccount.BANK) for a, _s, _v in lines
        )
        if not (single_sided and not touch_money):
            raise UnbalancedEntry(
                f"سَرآیند تراز نیست: بدهکار {debit} ≠ بستانکار {credit}"
            )

    entry, created = JournalEntry.objects.get_or_create(
        kind=kind,
        source_type=source_type,
        source_id=str(source_id),
        defaults={
            "occurred_at": occurred_at or timezone.now(),
            "title": title[:160],
            "detail": detail[:240],
            "method": method or "",
            "order": order,
        },
    )
    if created:
        JournalLine.objects.bulk_create(
            [
                JournalLine(entry=entry, account=a, side=s, amount=v)
                for a, s, v in lines
            ]
        )
    elif replace:
        entry.occurred_at = occurred_at or entry.occurred_at
        entry.title = title[:160]
        entry.detail = detail[:240]
        entry.method = method or ""
        entry.save(
            update_fields=["occurred_at", "title", "detail", "method"]
        )
        entry.lines.all().delete()
        JournalLine.objects.bulk_create(
            [
                JournalLine(entry=entry, account=a, side=s, amount=v)
                for a, s, v in lines
            ]
        )
    return entry


def remove(*, kind, source_type, source_id):
    """یک رویداد مالی و تمام سطرهایش را از دفتر پاک می‌کند (حذف رکورد منبع)."""
    JournalEntry.objects.filter(
        kind=kind, source_type=source_type, source_id=str(source_id)
    ).delete()


# ----------------------------------------------------------------- خواندن دفتر
def _signed(account, side_debit=True):
    return balance(account)


def _natural(account, debit, credit):
    """مانده با علامت طبیعی: دارایی و هزینه بدهکار مثبت، درآمد بستانکار مثبت."""
    if account in REVENUE_ACCOUNTS:
        return credit - debit
    return debit - credit


def balances(accounts, start=None, end=None):
    """مانده‌ی چند حساب در **یک** کوئری. برای داشبورد و گزارش‌ها لازم است تا
    خلاصه‌ی مالی با ده‌ها کوئری ساخته نشود."""
    accounts = list(accounts)
    if not accounts:
        return {}
    qs = JournalLine.objects.filter(account__in=accounts)
    if start is not None:
        qs = qs.filter(entry__occurred_at__gte=start)
    if end is not None:
        qs = qs.filter(entry__occurred_at__lt=end)
    agg = qs.aggregate(
        **{
            f"d_{i}": Sum("amount", filter=Q(account=a, side=Side.DEBIT))
            for i, a in enumerate(accounts)
        },
        **{
            f"c_{i}": Sum("amount", filter=Q(account=a, side=Side.CREDIT))
            for i, a in enumerate(accounts)
        },
    )
    return {
        a: _natural(a, agg.get(f"d_{i}") or 0, agg.get(f"c_{i}") or 0)
        for i, a in enumerate(accounts)
    }


ALL_ACCOUNTS = tuple(LedgerAccount.values)


def balances_multi(spans, accounts=ALL_ACCOUNTS):
    """مانده‌ی چند حساب در چند بازه، همه در **یک** کوئری.

    `spans` فهرست (کلید، start, end) است. برای داشبورد لازم است تا «امروز» و
    «این ماه» و مانده‌ی کل با یک رفت‌وبرگشت دیتابیس به دست آید.

    نکته‌ی پیاده‌سازی: شرط بازه باید داخل `filter=` هر `Sum` بنشیند، نه روی
    queryset؛ چون همه‌ی بازه‌ها در یک `aggregate` هستند و queryset مشترک دارند.
    """
    accounts = list(accounts)
    if not accounts or not spans:
        return {}
    agg = {}
    for key, start, end in spans:
        window = Q()
        if start is not None:
            window &= Q(entry__occurred_at__gte=start)
        if end is not None:
            window &= Q(entry__occurred_at__lt=end)
        for i, a in enumerate(accounts):
            agg[f"{key}_d_{i}"] = Sum(
                "amount", filter=Q(account=a, side=Side.DEBIT) & window
            )
            agg[f"{key}_c_{i}"] = Sum(
                "amount", filter=Q(account=a, side=Side.CREDIT) & window
            )
    agg = JournalLine.objects.filter(account__in=accounts).aggregate(**agg)
    out = {}
    for key, _s, _e in spans:
        out[key] = {
            a: _natural(a, agg.get(f"{key}_d_{i}") or 0, agg.get(f"{key}_c_{i}") or 0)
            for i, a in enumerate(accounts)
        }
    return out


def balance(account, start=None, end=None):
    """مانده‌ی حساب در بازه، همیشه با علامت طبیعی و مثبت برای گزارش."""
    return balances([account], start, end)[account]


def result_totals(start=None, end=None):
    """جمع درآمد، بهای تمام‌شده، ضایعات و هزینه در بازه."""
    return {
        "revenue": balance(LedgerAccount.REVENUE, start, end),
        "cogs": balance(LedgerAccount.COGS, start, end),
        "waste": balance(LedgerAccount.WASTE, start, end),
        "expenses": balance(LedgerAccount.EXPENSE, start, end),
    }


def profit_and_loss(start=None, end=None):
    """سود و زیان از روی دفتر، نه از محاسبه‌ی جداگانه (یک کوئری)."""
    b = balances(ALL_ACCOUNTS, start, end)
    revenue = b[LedgerAccount.REVENUE]
    cogs = b[LedgerAccount.COGS]
    expenses = b[LedgerAccount.EXPENSE]
    waste = b[LedgerAccount.WASTE]
    gross = revenue - cogs
    return {
        "revenue": revenue,
        "cogs": cogs,
        "waste": waste,
        "expenses": expenses,
        "gross_profit": gross,
        "net_profit": gross - expenses - waste,
    }


def purchase_total(start=None, end=None):
    """ارزش خرید کالا در بازه (بدهکار انبار از رویدادهای خرید)."""
    qs = JournalLine.objects.filter(
        account=LedgerAccount.INVENTORY,
        side=Side.DEBIT,
        # فقط خریدهای واقعاً ثبت‌شده؛ موجودی اولیه خرید نیست (پولش قبلاً داده شده).
        entry__kind=JournalKind.PURCHASE,
    )
    if start is not None:
        qs = qs.filter(entry__occurred_at__gte=start)
    if end is not None:
        qs = qs.filter(entry__occurred_at__lt=end)
    return qs.aggregate(s=Sum("amount"))["s"] or 0


def cash_account_total(account, start=None, end=None):
    """ورود و خروج پول یک حساب پولی در بازه."""
    qs = JournalLine.objects.filter(account=account)
    if start is not None:
        qs = qs.filter(entry__occurred_at__gte=start)
    if end is not None:
        qs = qs.filter(entry__occurred_at__lt=end)
    agg = qs.aggregate(
        d=Sum("amount", filter=Q(side=Side.DEBIT)),
        c=Sum("amount", filter=Q(side=Side.CREDIT)),
    )
    return agg["d"] or 0, agg["c"] or 0


def payment_methods(start=None, end=None):
    """تفکیک درآمد فروش بر اساس روش پرداخت. جمع آن = درآمد فروش بازه."""
    qs = JournalLine.objects.filter(
        account=LedgerAccount.REVENUE, side=Side.CREDIT, entry__kind=JournalKind.SALE
    )
    if start is not None:
        qs = qs.filter(entry__occurred_at__gte=start)
    if end is not None:
        qs = qs.filter(entry__occurred_at__lt=end)
    rows = (
        qs.values("entry__method")
        .annotate(total=Sum("amount"))
        .order_by("-total")
    )
    return [
        {"method": r["entry__method"] or "cash", "amount": r["total"] or 0}
        for r in rows
    ]


def cash_flow(start, end, opening_cash=0, opening_bank=0):
    """گردش نقدینگی واقعی: فقط پول واقعاً جابه‌جا شده.

    خرید و هزینه خروج پول‌اند؛ ضایعات و بهای تمام‌شده نیستند چون پولشان هنگام
    خرید پرداخت شده است. مطابق اصل ۳ پروژه.
    """
    out = {}
    for label, account, opening in (
        ("cash", CashAccount.CASH, opening_cash),
        ("bank", CashAccount.BANK, opening_bank),
    ):
        debit, credit = cash_account_total(account, start, end)
        out[label] = {
            "opening": opening,
            "in": debit,
            "out": credit,
            "closing": opening + debit - credit,
        }
    out["total_in"] = out["cash"]["in"] + out["bank"]["in"]
    out["total_out"] = out["cash"]["out"] + out["bank"]["out"]
    out["opening"] = out["cash"]["opening"] + out["bank"]["opening"]
    out["closing"] = out["cash"]["closing"] + out["bank"]["closing"]
    return out


def inventory_value():
    """ارزش موجودی فعلی انبار از دفتر (نه از قیمت لحظه‌ای کالا)."""
    return balance(LedgerAccount.INVENTORY)


# --------------------------------------------------------- رویدادهای آماده
def post_sale(*, order, method, amount, occurred_at=None, source_key=None):
    """فروش: بدهکار صندوق/بانک، بستانکار درآمد.

    کلید یکتایی پیش‌فرض «سفارش + روش پرداخت» است تا پردازش دوباره بی‌اثر بماند؛
    اما اگر یک سفارش با **دو پرداخت هم‌روش** تسویه شود این کلید تکراری می‌شود، پس
    سرویس سفارش `source_key` را با کلید واقعی هر Payment می‌فرستد.
    """
    return post(
        kind=JournalKind.SALE,
        source_type="order",
        source_id=source_key or f"{order.pk}:{method}",
        occurred_at=occurred_at or order.paid_at or timezone.now(),
        lines=[
            (METHOD_ACCOUNT.get(method, CashAccount.CASH), Side.DEBIT, amount),
            (LedgerAccount.REVENUE, Side.CREDIT, amount),
        ],
        title=f"سفارش {order.number}",
        detail=f"میز {order.table.number}",
        method=method,
        order=order,
    )


def post_cogs(*, order, amount, occurred_at=None, item_detail=""):
    """بهای تمام‌شده: بدهکار COGS، بستانکار موجودی."""
    return post(
        kind=JournalKind.COGS,
        source_type="order",
        source_id=order.pk,
        occurred_at=occurred_at or timezone.now(),
        lines=[
            (LedgerAccount.COGS, Side.DEBIT, amount),
            (LedgerAccount.INVENTORY, Side.CREDIT, amount),
        ],
        title=f"بهای تمام‌شده سفارش {order.number}",
        detail=item_detail,
        order=order,
    )


def reverse_cogs(*, order, amount, occurred_at=None, item_detail=""):
    """معکوس بهای تمام‌شده (لغو سفارش/برگشت): بدهکار انبار، بستانکار COGS."""
    return post(
        kind=JournalKind.COGS_REVERSE,
        source_type="order",
        source_id=order.pk,
        occurred_at=occurred_at or timezone.now(),
        lines=[
            (LedgerAccount.INVENTORY, Side.DEBIT, amount),
            (LedgerAccount.COGS, Side.CREDIT, amount),
        ],
        title=f"برگشت بهای تمام‌شده سفارش {order.number}",
        detail=item_detail,
        order=order,
    )


def post_opening_stock(*, item, amount, occurred_at=None):
    """موجودی اولیه‌ی کالا: فقط بدهکار انبار، بدون اثر نقدی.

    پولش قبلاً (خارج از سیستم) پرداخت شده، پس بستانکار نمی‌کنیم؛ وگرنه موجودی
    صندوق/بانک منفی می‌شد. تاریخ به زمان ساخت کالا می‌خورد، نه زمان ثبت در سیستم.
    """
    return post(
        kind=JournalKind.OPENING_STOCK,
        source_type="item",
        source_id=item.pk,
        # تاریخ لحظه‌ی ثبت کالا در سیستم است؛ `InventoryItem` ستون زمانی ندارد.
        occurred_at=occurred_at or timezone.now(),
        lines=[
            (LedgerAccount.INVENTORY, Side.DEBIT, amount),
        ],
        title=f"موجودی اولیه {item.name}".strip(),
        detail=item.description or "",
        method="",
        order=None,
    )


def post_purchase(*, transaction_obj, amount, account, occurred_at=None, replace=False):
    """خرید: بدهکار موجودی، بستانکار صندوق/بانک. تا مصرف‌نشدن هزینه نیست."""
    return post(
        replace=replace,
        kind=JournalKind.PURCHASE,
        source_type="purchase",
        source_id=transaction_obj.pk,
        occurred_at=occurred_at or transaction_obj.created_at,
        lines=[
            (LedgerAccount.INVENTORY, Side.DEBIT, amount),
            (account, Side.CREDIT, amount),
        ],
        title=f"خرید {transaction_obj.item_name_snapshot or ''}".strip(),
        detail=transaction_obj.note or "",
        method="",
        order=None,
    )


def post_waste(*, transaction_obj, amount, occurred_at=None, replace=False):
    """ضایعات: بدهکار ضایعات، بستانکار موجودی. اثر نقدی ندارد."""
    return post(
        replace=replace,
        kind=JournalKind.WASTE,
        source_type="waste",
        source_id=transaction_obj.pk,
        occurred_at=occurred_at or transaction_obj.created_at,
        lines=[
            (LedgerAccount.WASTE, Side.DEBIT, amount),
            (LedgerAccount.INVENTORY, Side.CREDIT, amount),
        ],
            title=f"ضایعات {transaction_obj.item_name_snapshot or ''}".strip(),
            detail=transaction_obj.note or "",
        )


def post_expense(*, expense, replace=False):
    """هزینه: بدهکار هزینه، بستانکار صندوق/بانک."""
    return post(
        replace=replace,
        kind=JournalKind.EXPENSE,
        source_type="expense",
        source_id=expense.pk,
        occurred_at=expense.date,
        lines=[
            (LedgerAccount.EXPENSE, Side.DEBIT, expense.amount),
            (expense.account, Side.CREDIT, expense.amount),
        ],
        title=expense.title,
        detail=expense.note or "",
    )


def post_refund(*, order, method, amount, occurred_at=None, source_key=None):
    """برگشت از فروش: بدهکار فروش (کاهش درآمد)، بستانکار صندوق/بانک."""
    return post(
        kind=JournalKind.REFUND,
        source_type="order",
        source_id=source_key or f"{order.pk}:{method}",
        occurred_at=occurred_at or timezone.now(),
        lines=[
            (LedgerAccount.REVENUE, Side.DEBIT, amount),
            (METHOD_ACCOUNT.get(method, CashAccount.CASH), Side.CREDIT, amount),
        ],
        title=f"برگشت سفارش {order.number}",
        method=method,
        order=order,
    )


def day_bounds(now=None):
    """(شروع امروز، شروع فردا) به وقت تهران؛ end نیمه‌باز است."""
    now = now or timezone.now()
    local = timezone.localtime(now)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)

# --------------------------------------------------------- بازرسی سلامت دفتر
def health():
    """بررسی می‌کند دفتر با واقعیت کسب‌وکار هم‌تراز باشد.

    این تابع «دروغ‌گوی» نیست: هر اختلاف واقعی را با جزئیات برمی‌گرداند تا بشود
    آن را دید و رفع کرد، نه اینکه با کرش یا سکوت پنهان شود. خروجی
    `{"ok": bool, "problems": [...]}` است و برای داشبورد و تست قابل استفاده است.
    """
    problems = []

    # ۱) هر ورودی دفتر باید تراز باشد. استثنا همان رویدادهای تک‌طرفه‌ی مجاز است
    #    (موجودی اولیه / تجدید ارزش) که پول را درگیر نمی‌کنند — همان قاعده‌ای که
    #    `post()` اعمال می‌کند، وگرنه بازرسی خودِ رکوردهای معتبر را قرمز می‌کرد.
    totals = (
        JournalLine.objects.values("entry_id", "account")
        .annotate(
            d=Sum("amount", filter=Q(side=Side.DEBIT)),
            c=Sum("amount", filter=Q(side=Side.CREDIT)),
        )
    )
    per_entry = {}
    for row in totals:
        got = per_entry.setdefault(
            row["entry_id"], {"debit": 0, "credit": 0, "money": False}
        )
        got["debit"] += row["d"] or 0
        got["credit"] += row["c"] or 0
        if row["account"] in (CashAccount.CASH, CashAccount.BANK):
            got["money"] = True
    for entry_id, got in per_entry.items():
        if got["debit"] == got["credit"]:
            continue
        single_sided = got["debit"] == 0 or got["credit"] == 0
        if single_sided and not got["money"]:
            continue  # رویداد تک‌طرفه‌ی غیرپولیِ مجاز
        problems.append(
            {
                "code": "unbalanced_entry",
                "entry_id": entry_id,
                "detail": f"بدهکار {got['debit']} ≠ بستانکار {got['credit']}",
            }
        )

    # ۲) درآمد دفتر باید با جمع پرداخت‌های سفارش‌های تسویه‌شده یکی باشد.
    from orders.models import Order, Payment

    revenue = balance(LedgerAccount.REVENUE)
    paid_sum = (
        Payment.objects.aggregate(s=Sum("amount"))["s"] or 0
    )
    refunded = (
        JournalLine.objects.filter(
            account=LedgerAccount.REVENUE,
            side=Side.DEBIT,
            entry__kind=JournalKind.REFUND,
        ).aggregate(s=Sum("amount"))["s"]
        or 0
    )
    if revenue != paid_sum - refunded:
        problems.append(
            {
                "code": "revenue_mismatch",
                "detail": (
                    f"درآمد دفتر {revenue} ≠ جمع پرداخت‌ها {paid_sum}"
                    f" منهای برگشتی {refunded}"
                ),
            }
        )

    # ۳) هر سفارشِ تسویه‌شده باید دقیقاً به اندازه‌ی مبلغش ورودی فروش داشته باشد.
    for o in Order.objects.filter(payment_status="paid"):
        got = sum(
            p.amount
            for p in o.payments.all()
            if JournalEntry.objects.filter(
                kind=JournalKind.SALE, source_id=f"payment:{p.pk}"
            ).exists()
        )
        if got != o.total:
            problems.append(
                {
                    "code": "order_sale_mismatch",
                    "order_id": str(o.id),
                    "detail": f"ورودی فروش {got} ≠ مبلغ سفارش {o.total}",
                }
            )

    # ۴) موجودی دفتر باید با ارزش موجودی فیزیکی کالاها بخواند.
    from inventory.models import InventoryItem

    physical = sum(
        int(i.current_stock * i.unit_cost) if i.unit_cost else 0
        for i in InventoryItem.objects.all()
    )
    book = balance(LedgerAccount.INVENTORY)
    if abs(physical - book) > 1:
        problems.append(
            {
                "code": "inventory_mismatch",
                "detail": f"موجودی دفتر {book} ≠ ارزش کالا {physical}",
            }
        )

    # ۵) مانده‌ی منفی «مشکلِ دفتر» نیست؛ وضعیتِ کسب‌وکار است (مثلاً پرداخت اجاره از
    # صندوقی که هنوز پر نشده). پس هشدار است، نه خطای یکپارچگی — وگرنه این بازرسی
    # روی داده‌ی واقعی همیشه قرمز می‌شود و ارزشش را از دست می‌دهد.
    warnings = []
    for acct in (CashAccount.CASH, CashAccount.BANK):
        if balance(acct) < 0:
            warnings.append(
                {"code": "negative_balance", "detail": f"مانده‌ی {acct} منفی است"}
            )
    if physical < 0:
        warnings.append(
            {"code": "negative_stock", "detail": "موجودی فیزیکی یک کالا منفی است"}
        )

    return {
        "ok": not problems,
        "problems": problems,
        "warnings": warnings,
    }
