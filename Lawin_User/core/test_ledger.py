"""تست‌های دفتر حسابداری مرکزی: تراز، یکتایی، و هم‌ترازی دفتر با واقعیت.

این‌ها تست‌های «قانون مالی» هستند: اگر یکی از آن‌ها بشکند یعنی یک تغییر
واقعاً مالی انجام شده بی‌آنکه در دفتر ثبت شود، یا گزارش‌ها از هم جدا شوند.
"""

from decimal import Decimal

from django.test import Client, TestCase
from django.utils import timezone

from core import ledger
from core.models import (
    CashAccount,
    Expense,
    JournalEntry,
    JournalKind,
    JournalLine,
    LedgerAccount,
    Side,
)
from core.security import TICKET_HEADER
from core.testing import make_financial_world, make_world
from inventory import services as inventory_services
from inventory.models import InventoryTransaction
from orders import services as order_services


class LedgerBalanceTests(TestCase):
    def setUp(self):
        self.w = make_world()

    def test_unbalanced_entry_is_rejected(self):
        """ورودی نامتوازن نباید هرگز ثبت شود."""
        with self.assertRaises(ledger.UnbalancedEntry):
            ledger.post(
                kind=JournalKind.EXPENSE,
                source_type="test",
                source_id="x",
                lines=[
                    (LedgerAccount.EXPENSE, Side.DEBIT, 1000),
                    (CashAccount.CASH, Side.CREDIT, 999),
                ],
            )
        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_single_sided_money_entry_is_rejected(self):
        """پول نمی‌تواند از هیچ‌جا بیاید یا بی‌جا ناپدید شود."""
        with self.assertRaises(ledger.UnbalancedEntry):
            ledger.post(
                kind=JournalKind.EXPENSE,
                source_type="test",
                source_id="y",
                lines=[(CashAccount.CASH, Side.DEBIT, 500)],
            )

    def test_post_is_idempotent_per_source(self):
        """پردازش دوباره‌ی یک رویداد نباید دفتر را دو برابر کند."""
        args = dict(
            kind=JournalKind.EXPENSE, source_type="test", source_id="z",
            lines=[(LedgerAccount.EXPENSE, Side.DEBIT, 700),
                   (CashAccount.CASH, Side.CREDIT, 700)],
        )
        ledger.post(**args)
        ledger.post(**args)
        self.assertEqual(JournalEntry.objects.filter(source_id="z").count(), 1)
        self.assertEqual(ledger.balance(LedgerAccount.EXPENSE), 700)

    def test_replace_swaps_amount_instead_of_adding(self):
        """ویرایش مبلغ باید جایگزین کند، نه جمع شود."""
        e = Expense.objects.create(
            title="اجاره", amount=1000, category="rent", date=timezone.now()
        )
        self.assertEqual(ledger.balance(LedgerAccount.EXPENSE), 1000)
        e.amount = 400
        e.save()  # سیگنالِ هم‌ترازی، دفتر را به‌روز می‌کند
        self.assertEqual(ledger.balance(LedgerAccount.EXPENSE), 400)
        self.assertEqual(
            JournalLine.objects.filter(account=CashAccount.CASH).count(), 1
        )

    def test_delete_removes_the_entry(self):
        e = Expense.objects.create(
            title="اجاره", amount=2500, category="rent", date=timezone.now()
        )
        e.delete()
        self.assertEqual(ledger.balance(LedgerAccount.EXPENSE), 0)
        self.assertEqual(JournalEntry.objects.filter(kind=JournalKind.EXPENSE).count(), 0)

    def test_signs_are_natural_for_reports(self):
        """درآمد و هزینه هر دو مثبت برمی‌گردند تا جمع ساده معنادار باشد."""
        o = order_services.create_order(
            table_id=self.w.t2.id, source="admin",
            items=[{"product_id": self.w.cake.id, "quantity": 2}],
        )
        order_services.pay_order(o.id, "cash", self.w.admin)
        Expense.objects.create(
            title="اجاره", amount=900000, category="rent", date=timezone.now()
        )
        pl = ledger.profit_and_loss()
        self.assertEqual(pl["revenue"], 220000)
        self.assertEqual(pl["expenses"], 900000)
        self.assertEqual(pl["net_profit"], 220000 - 900000)
        self.assertEqual(
            JournalLine.objects.get(
                entry__kind=JournalKind.SALE, account=LedgerAccount.REVENUE
            ).side,
            Side.CREDIT,
        )

    def test_cogs_uses_purchased_price_not_sale_price(self):
        """بهای تمام‌شده از قیمت خرید کالا می‌آید، نه از قیمت فروش محصول.

        قیمت‌های واقع‌گرایانه: لاته ۹۵٬۰۰۰ فروش می‌رود و موادش ۶۴٬۴۰۰ هزینه دارد
        (شیر ۲۰۰ml × ۲۵۰ + قهوه ۱۸g × ۸۰۰). اگر کد به‌جای قیمت خرید، قیمت فروش
        محصول را به‌عنوان بهای تمام‌شده می‌گرفت، این عدد می‌شد ۹۵٬۰۰۰.
        """
        milk, beans = self.w.milk, self.w.beans
        inventory_services.create_purchase(
            {"item_id": milk.id, "quantity": "1000", "unit_cost": "250",
             "account": CashAccount.CASH}
        )
        inventory_services.create_purchase(
            {"item_id": beans.id, "quantity": "500", "unit_cost": "800",
             "account": CashAccount.CASH}
        )
        milk.refresh_from_db()  # سرویس، نسخه‌ی خودش را در دیتابیس به‌روز کرد
        self.assertEqual(milk.unit_cost, Decimal("250"))

        o = order_services.create_order(
            table_id=self.w.t2.id, source="admin",
            items=[{"product_id": self.w.latte.id, "quantity": 1}],
        )
        order_services.pay_order(o.id, "cash", self.w.admin)

        pl = ledger.profit_and_loss()
        self.assertEqual(pl["revenue"], 95000)
        self.assertEqual(pl["cogs"], 200 * 250 + 18 * 800)  # ۶۴٬۴۰۰
        self.assertEqual(pl["gross_profit"], 95000 - 64400)

        # پرداخت بابت خرید، هزینه نیست: فقط موجودی و نقدینگی را عوض می‌کند.
        cash = ledger.cash_flow(*ledger.day_bounds())
        self.assertEqual(cash["cash"]["out"], 1000 * 250 + 500 * 800)
        self.assertEqual(pl["expenses"], 0)
        self.assertEqual(pl["waste"], 0)

    def test_cogs_snapshot_survives_a_price_change(self):
        """تغییر بعدی قیمت کالا نباید بهای تمام‌شده‌ی فروش گذشته را تغییر دهد."""
        milk = self.w.milk
        inventory_services.update_item(milk.id, {"unit_cost": "250"})
        o = order_services.create_order(
            table_id=self.w.t2.id, source="admin",
            items=[{"product_id": self.w.latte.id, "quantity": 1}],
        )
        order_services.pay_order(o.id, "cash", self.w.admin)
        before = ledger.profit_and_loss()["cogs"]

        inventory_services.update_item(milk.id, {"unit_cost": "999"})
        self.assertEqual(ledger.profit_and_loss()["cogs"], before)


class LedgerHealthTests(TestCase):
    def setUp(self):
        self.w = make_financial_world()
        self.api = Client(
            headers={
                "Authorization": f"Token {self.w.token}",
                TICKET_HEADER: self.w.security_ticket,
            }
        )

    def _healthy_world(self):
        o = order_services.create_order(
            table_id=self.w.t2.id, source="admin",
            items=[{"product_id": self.w.cake.id, "quantity": 2}],
        )
        order_services.pay_order(o.id, "card_reader", self.w.admin)
        Expense.objects.create(
            title="اجاره", amount=50000, category="rent", date=timezone.now()
        )

    def test_healthy_world_passes_verify(self):
        self._healthy_world()
        body = self.api.get("/api/v1/accounting/verify/").json()
        self.assertTrue(body["ok"], body["problems"])

    def test_verify_requires_token(self):
        self.assertEqual(Client().get("/api/v1/accounting/verify/").status_code, 401)

    def test_revenue_must_match_payments(self):
        """دستکاری مستقیم دیتابیس باید در بازرسی لو برود."""
        self._healthy_world()
        JournalLine.objects.filter(
            entry__kind=JournalKind.SALE, account=LedgerAccount.REVENUE
        ).update(amount=1)
        body = self.api.get("/api/v1/accounting/verify/").json()
        self.assertFalse(body["ok"])
        self.assertIn("revenue_mismatch", [p["code"] for p in body["problems"]])

    def test_inventory_must_match_ledger(self):
        """موجودی فیزیکی و دفتر باید بخوانند."""
        self._healthy_world()
        # قیمت را از مسیر سرویس عوض می‌کنیم تا دفتر هم به‌روز شود
        inventory_services.update_item(self.w.milk.id, {"unit_cost": "250"})
        health = ledger.health()
        self.assertTrue(health["ok"], health["problems"])

        # حالا موجودی را مستقیم بالا می‌بریم: ارزش کالا عوض می‌شود ولی هیچ سندی
        # در دفتر ثبت نمی‌شود ⇒ بازرسی باید اختلاف را لو بدهد.
        self.w.milk.current_stock += Decimal("500")
        self.w.milk.save()
        body = self.api.get("/api/v1/accounting/verify/").json()
        self.assertFalse(body["ok"])
        self.assertIn("inventory_mismatch", [p["code"] for p in body["problems"]])

    def test_price_change_is_posted_as_revaluation(self):
        """تغییر قیمت کالای موجود، ارزش انبار را در دفتر هم جابه‌جا می‌کند."""
        inventory_services.update_item(self.w.milk.id, {"unit_cost": "250"})
        # ۱۰۰۰ml × ۲۵۰ = ۲۵۰٬۰۰۰ ارزش موجودی، که پیش‌تر بی‌بها بود
        self.assertEqual(ledger.balance(LedgerAccount.INVENTORY), 250000)
        inventory_services.update_item(self.w.milk.id, {"unit_cost": "500"})
        self.assertEqual(ledger.balance(LedgerAccount.INVENTORY), 500000)
        self.assertEqual(ledger.balance(CashAccount.CASH), 0)  # پول جابه‌جا نشد

    def test_price_drop_credits_inventory_without_touching_money(self):
        """افت قیمت، بستانکارِ انبار است و باز هم پولی جابه‌جا نمی‌کند."""
        inventory_services.create_purchase(
            {"item_id": self.w.milk.id, "quantity": "1000",
             "unit_cost": "500", "account": CashAccount.CASH}
        )
        inventory_services.update_item(self.w.milk.id, {"unit_cost": "200"})
        self.w.milk.refresh_from_db()
        self.assertEqual(
            ledger.balance(LedgerAccount.INVENTORY),
            int(self.w.milk.current_stock * self.w.milk.unit_cost),
        )
        self.assertEqual(ledger.balance(CashAccount.CASH), -500000)
        self.assertTrue(ledger.health()["ok"], ledger.health()["problems"])

    def test_unchanged_price_posts_nothing(self):
        """همان قیمت دوباره ⇒ نه سطر انبار، نه سند دفتر."""
        inventory_services.create_purchase(
            {"item_id": self.w.milk.id, "quantity": "10",
             "unit_cost": "250", "account": CashAccount.CASH}
        )

        def revaluations():
            return InventoryTransaction.objects.filter(
                kind=InventoryTransaction.Kind.REVALUATION
            ).count()

        before = revaluations()
        inventory_services.update_item(self.w.milk.id, {"unit_cost": "250"})
        self.assertEqual(revaluations(), before)

    def test_negative_cash_is_a_warning_not_an_error(self):
        """پرداخت بیشتر از موجودی، وضعیت کسب‌وکار است نه خرابی دفتر."""
        o = order_services.create_order(
            table_id=self.w.t2.id, source="admin",
            items=[{"product_id": self.w.cake.id, "quantity": 1}],
        )
        order_services.pay_order(o.id, "cash", self.w.admin)
        Expense.objects.create(
            title="اجاره", amount=9_000_000, category="rent", date=timezone.now()
        )
        body = self.api.get("/api/v1/accounting/verify/").json()
        self.assertTrue(body["ok"], body["problems"])
        self.assertIn("negative_balance", [w["code"] for w in body["warnings"]])