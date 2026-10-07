"""پر کردن دفتر حسابداری از داده‌های موجود و حذف دسته‌ی «خرید مواد اولیه».

هدف: تاریخچه‌ی مالی قبلی پس از اضافه شدن دفتر مرکزی نباید ناپدید شود. تمام
سفارش‌های پرداخت‌شده، خریدها، ضایعات، هزینه‌ها و مصرف موادِ از پیش موجود به
دفتر دوطرفه منتقل می‌شوند تا گزارش‌ها با داده‌ی واقعی گذشته بخوانند.

توجه: تراکنش‌های مصرفِ قدیمی unit_cost ندارند (پیش از این تغییر ذخیره نمی‌شد)؛
برای آن‌ها قیمت خرید فعلی کالا به‌عنوان بهای تمام‌شده‌ی تقریبی به کار می‌رود.
سفارش‌های آینده این مقدار را به‌صورت snapshot در همان لحظه ذخیره می‌کنند.
"""

from decimal import Decimal

from django.db import migrations

ACCOUNT_CASH, ACCOUNT_BANK = "cash", "bank"
METHOD_ACCOUNT = {
    "cash": ACCOUNT_CASH,
    "card_reader": ACCOUNT_BANK,
    "card_transfer": ACCOUNT_BANK,
}


def _money(qty, cost):
    """ارزش خطی به تومانِ صحیح؛ تایم‌کد ریال به تومان است و جمع نهایی باید int باشد."""
    return int(Decimal(qty or 0) * Decimal(cost or 0))


def backfill(apps, schema_editor):
    JournalEntry = apps.get_model("core", "JournalEntry")
    JournalLine = apps.get_model("core", "JournalLine")
    Expense = apps.get_model("core", "Expense")
    InventoryItem = apps.get_model("inventory", "InventoryItem")
    InventoryTransaction = apps.get_model("inventory", "InventoryTransaction")
    Order = apps.get_model("orders", "Order")
    Payment = apps.get_model("orders", "Payment")

    def post(kind, source_type, source_id, occurred_at, lines, title="", detail="", method="", order_id=None):
        lines = [(a, s, int(v)) for a, s, v in lines if int(v) > 0]
        if not lines:
            return None
        if sum(v for _, s, v in lines if s == "debit") != sum(
            v for _, s, v in lines if s == "credit"
        ):
            raise RuntimeError(f"unbalanced backfill: {kind} {source_id}")
        entry, created = JournalEntry.objects.get_or_create(
            kind=kind,
            source_type=source_type,
            source_id=str(source_id),
            defaults={
                "occurred_at": occurred_at,
                "title": title[:160],
                "detail": detail[:240],
                "method": method or "",
                "order_id": order_id,
            },
        )
        if created:
            JournalLine.objects.bulk_create(
                [
                    JournalLine(entry_id=entry.id, account=a, side=s, amount=v)
                    for a, s, v in lines
                ]
            )
        return entry

    # ۱) دسته‌ی حذف‌شده: داده‌ی موجود به «سایر» منتقل می‌شود، نه پاک.
    Expense.objects.filter(category="raw_materials").update(category="other")

    # ۲) فروش: هر پرداخت یک ورودی نقد + درآمد.
    for p in Payment.objects.select_related("order", "order__table").iterator():
        o = p.order
        post(
            "sale", "order", f"{o.pk}:{p.method}", p.created_at or o.paid_at,
            [
                (METHOD_ACCOUNT.get(p.method, ACCOUNT_CASH), "debit", p.amount),
                ("revenue", "credit", p.amount),
            ],
            title=f"سفارش {o.number}", detail=f"میز {o.table.number}",
            method=p.method, order_id=o.pk,
        )

    # ۳) بهای تمام‌شده: مصرف مواد هر سفارشِ پرداخت‌شده.
    costs = {}
    for t in InventoryTransaction.objects.filter(
        kind="order_consume", order__status="paid"
    ).select_related("order", "item"):
        value = _money(abs(t.quantity), t.unit_cost or t.item.unit_cost)
        if value > 0:
            costs[t.order_id] = costs.get(t.order_id, 0) + value
    for order_id, value in costs.items():
        o = Order.objects.get(pk=order_id)
        post(
            "cogs", "order", o.pk, o.paid_at or o.created_at,
            [("cogs", "debit", value), ("inventory", "credit", value)],
            title=f"بهای تمام‌شده سفارش {o.number}", order_id=o.pk,
        )

    # ۴) خرید: موجودی انبار در برابر خروج پول.
    for t in InventoryTransaction.objects.filter(kind="purchase").select_related("item"):
        value = _money(abs(t.quantity), t.unit_cost or t.item.unit_cost)
        post(
            "purchase", "purchase", t.pk, t.created_at,
            [("inventory", "debit", value), (ACCOUNT_CASH, "credit", value)],
            title=f"خرید {t.item_name_snapshot or t.item.name}".strip(),
        )

    # ۵) ضایعات: کاهش موجودی، بدون اثر نقدی.
    for t in InventoryTransaction.objects.filter(kind="waste").select_related("item"):
        value = _money(abs(t.quantity), t.unit_cost or t.item.unit_cost)
        post(
            "waste", "waste", t.pk, t.created_at,
            [("waste", "debit", value), ("inventory", "credit", value)],
            title=f"ضایعات {t.item_name_snapshot or t.item.name}".strip(),
        )

    # ۶) موجودی اولیه‌ی کالا (ADJUST مثبت) به‌عنوان ورود انبار.
    for t in InventoryTransaction.objects.filter(kind="adjust", quantity__gt=0).select_related("item"):
        value = _money(t.quantity, t.unit_cost or t.item.unit_cost)
        post(
            "purchase", "adjust", t.pk, t.created_at,
            [("inventory", "debit", value), (ACCOUNT_CASH, "credit", value)],
            title=f"موجودی اولیه {t.item_name_snapshot or t.item.name}".strip(),
        )

    # ۷) هزینه‌های عملیاتی.
    for e in Expense.objects.all():
        post(
            "expense", "expense", e.pk, e.date,
            [("expense", "debit", e.amount), (e.account or ACCOUNT_CASH, "credit", e.amount)],
            title=e.title,
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_cafesettings_opening_bank_cafesettings_opening_cash_and_more"),
        ("orders", "0002_alter_payment_options_order_idempotency_key_and_more"),
        ("inventory", "0004_inventorytransaction_item_name_snapshot"),
    ]

    operations = [migrations.RunPython(backfill, noop)]