"""تست‌های جریان پول: پرداخت چندروشی، برگشت کامل، و اصلاح خرید/ضایعات.

این‌ها مسیرهایی هستند که قبلاً اصلاً وجود نداشتند (برگشت، پرداخت ناقص، ویرایش
خرید) یا اشتباه ثبت می‌شدند؛ بنابراین هم رفتار درست را قفل می‌کنند و هم
اثرشان روی موجودی، نقدینگی و سود و زیان را می‌سنجند.
"""

from decimal import Decimal

from django.test import Client, TestCase

from core import ledger
from core.errors import Conflict, Invalid, OutOfStock
from core.models import CashAccount, JournalKind, LedgerAccount
from core.testing import make_world
from inventory import services as inventory_services
from orders import services as order_services
from orders.models import Payment


class PaymentSplitTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.order = order_services.create_order(
            table_id=self.w.t2.id, source="admin",
            items=[{"product_id": self.w.cake.id, "quantity": 2}],  # ۲۲۰٬۰۰۰
        )

    def test_two_methods_split_sale_into_two_entries(self):
        """پرداخت نقدی+کارت باید دو ورودی فروش مستقل بسازد."""
        order_services.pay_order(
            self.order.id, "cash", self.w.admin,
            payments=[{"method": "cash", "amount": 120000},
                      {"method": "card_reader", "amount": 100000}],
        )
        self.order.refresh_from_db()  # سرویس نسخه‌ی خودش را ذخیره کرده
        self.assertEqual(self.order.payment_status, "paid")
        self.assertEqual(
            sorted(p.amount for p in Payment.objects.filter(order=self.order)),
            [100000, 120000],
        )
        methods = ledger.payment_methods()
        self.assertEqual(
            sorted((m["method"], m["amount"]) for m in methods),
            [("card_reader", 100000), ("cash", 120000)],
        )

    def test_two_payments_of_the_same_method_are_both_posted(self):
        """دو پرداخت هم‌روش نباید در قید یکتایی گم شود.

        کلید ورودی فروش روی خودِ Payment است، نه روی «سفارش+روش»؛ وگرنه
        پرداخت دوم بی‌سروصدا حذف می‌شد و درآمد کمتر از واقع گزارش می‌شد.
        """
        order_services.pay_order(
            self.order.id, "cash", self.w.admin,
            payments=[{"method": "cash", "amount": 100000},
                      {"method": "cash", "amount": 120000}],
        )
        self.assertEqual(ledger.balance(LedgerAccount.REVENUE), 220000)
        self.assertEqual(Payment.objects.filter(order=self.order).count(), 2)

    def test_split_sum_must_match_total_exactly(self):
        with self.assertRaises(Invalid):
            order_services.pay_order(
                self.order.id, "cash", self.w.admin,
                payments=[{"method": "cash", "amount": 100000},
                          {"method": "card_reader", "amount": 100000}],
            )
        self.assertEqual(ledger.balance(LedgerAccount.REVENUE), 0)
        self.assertEqual(Payment.objects.count(), 0)

    def test_non_positive_amount_is_rejected(self):
        with self.assertRaises(Invalid):
            order_services.pay_order(
                self.order.id, "cash", self.w.admin,
                payments=[{"method": "cash", "amount": 220000},
                          {"method": "card_reader", "amount": -1}],
            )

    def test_unknown_method_is_rejected(self):
        with self.assertRaises(Invalid):
            order_services.pay_order(self.order.id, "gold", self.w.admin)

    def test_double_pay_is_rejected_and_ledger_stays_single(self):
        order_services.pay_order(self.order.id, "cash", self.w.admin)
        with self.assertRaises(Conflict):
            order_services.pay_order(self.order.id, "cash", self.w.admin)
        self.assertEqual(ledger.balance(LedgerAccount.REVENUE), 220000)

    def test_bank_payment_goes_to_bank_not_cash(self):
        """کارتخوان و کارت‌به‌کارت باید در حساب بانک بنشینند."""
        order_services.pay_order(self.order.id, "card_transfer", self.w.admin)
        self.assertEqual(ledger.balance(CashAccount.CASH), 0)
        self.assertEqual(ledger.balance(CashAccount.BANK), 220000)

    def test_pay_table_allocates_split_across_orders(self):
        """در پرداخت میز، مبلغ‌های چندروشی بین سفارش‌ها پخش می‌شود."""
        order_services.create_order(
            table_id=self.w.t2.id, source="admin",
            items=[{"product_id": self.w.cake.id, "quantity": 1}],  # ۱۱۰٬۰۰۰
        )
        order_services.pay_table(
            self.w.t2.id, "cash", self.w.admin,
            payments=[{"method": "cash", "amount": 300000},
                      {"method": "card_reader", "amount": 30000}],
        )
        per_order = {}
        for p in Payment.objects.all():
            per_order[p.order_id] = per_order.get(p.order_id, 0) + p.amount
        self.assertEqual(sorted(per_order.values()), [110000, 220000])
        # سفارش دوم دو تکه پرداخت دارد ⇒ ۳ سطر Payment در مجموع
        self.assertEqual(Payment.objects.count(), 3)
        self.assertEqual(ledger.balance(LedgerAccount.REVENUE), 330000)


class RefundTests(TestCase):
    def setUp(self):
        self.w = make_world()
        # قیمت خرید تا بهای تمام‌شده صفر نباشد
        inventory_services.update_item(
            self.w.milk.id, {"unit_cost": "250"}
        )
        self.order = order_services.create_order(
            table_id=self.w.t2.id, source="admin",
            items=[{"product_id": self.w.latte.id, "quantity": 1}],
        )
        order_services.pay_order(self.order.id, "cash", self.w.admin)

    def test_refund_needs_a_paid_order(self):
        open_ = order_services.create_order(
            table_id=self.w.t5.id, source="admin",
            items=[{"product_id": self.w.cake.id, "quantity": 1}],
        )
        with self.assertRaises(Conflict):
            order_services.refund_order(open_.id, self.w.admin)

    def test_refund_cannot_run_twice(self):
        order_services.refund_order(self.order.id, self.w.admin)
        with self.assertRaises(Conflict):
            order_services.refund_order(self.order.id, self.w.admin)

    def test_refund_reverses_revenue_cash_and_stock(self):
        self.w.milk.refresh_from_db()
        stock_before = self.w.milk.current_stock
        pl_before = ledger.profit_and_loss()
        self.assertEqual(pl_before["revenue"], 95000)
        self.assertGreater(pl_before["cogs"], 0)

        order_services.refund_order(self.order.id, self.w.admin)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "refunded")
        pl_after = ledger.profit_and_loss()
        self.assertEqual(pl_after["revenue"], 0)  # درآمد برگشتی خنثی شد
        self.assertEqual(pl_after["cogs"], 0)  # بهای تمام‌شده هم برگشت
        self.assertEqual(ledger.balance(CashAccount.CASH), 0)
        self.w.milk.refresh_from_db()
        self.assertEqual(self.w.milk.current_stock, stock_before + Decimal("200"))

    def test_refund_of_split_payment_returns_each_account(self):
        """پول دقیقاً از همان حسابی برمی‌گردد که پرداخت شده بود."""
        self.w.milk.current_stock += Decimal("500")
        self.w.milk.save()
        before_cash = ledger.balance(CashAccount.CASH)
        before_bank = ledger.balance(CashAccount.BANK)
        order = order_services.create_order(
            table_id=self.w.t5.id, source="admin",
            items=[{"product_id": self.w.latte.id, "quantity": 1}],
        )
        order_services.pay_order(
            order.id, "cash", self.w.admin,
            payments=[{"method": "cash", "amount": 50000},
                      {"method": "card_reader", "amount": 45000}],
        )
        order_services.refund_order(order.id, self.w.admin)
        self.assertEqual(ledger.balance(CashAccount.CASH), before_cash)
        self.assertEqual(ledger.balance(CashAccount.BANK), before_bank)
        self.assertEqual(ledger.balance(LedgerAccount.REVENUE), 95000)  # فقط سفارش اول

    def test_refund_blocked_when_stock_already_consumed(self):
        """اگر مواد مصرف‌شده را نباید برگرداند، برگشت هم نباید ثبت شود."""
        order_services.refund_order(self.order.id, self.w.admin)  # موجودی برگشت
        self.w.milk.current_stock = Decimal("0")
        self.w.milk.save()  # انبار خالی شد
        with self.assertRaises(Conflict):
            order_services.refund_order(self.order.id, self.w.admin)


class PurchaseEditTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.milk = self.w.milk
        self.milk.refresh_from_db()
        self.stock_start = self.milk.current_stock
        self.p = inventory_services.create_purchase(
            {"item_id": self.milk.id, "quantity": "100", "unit_cost": "250",
             "account": CashAccount.BANK, "note": "خرید اول"}
        )
        self.milk.refresh_from_db()

    def test_purchase_replaces_the_money_not_accumulate(self):
        self.assertEqual(ledger.balance(CashAccount.BANK), -25000)
        inventory_services.update_purchase(self.p.pk, {"quantity": "200"})
        self.assertEqual(ledger.balance(CashAccount.BANK), -50000)
        self.milk.refresh_from_db()
        # ۱۰۰۰ اولیه + ۱۰۰ خرید اول، حالا مقدار خرید به ۲۰۰ اصلاح شد:
        # ۱۰۰ قدیمی برمی‌گردد و ۲۰۰ تازه اضافه می‌شود ⇒ ۱۲۰۰
        self.assertEqual(self.milk.current_stock, self.stock_start + 200)

    def test_switching_account_moves_the_money(self):
        inventory_services.update_purchase(self.p.pk, {"account": CashAccount.CASH})
        self.assertEqual(ledger.balance(CashAccount.BANK), 0)
        self.assertEqual(ledger.balance(CashAccount.CASH), -25000)

    def test_delete_purchase_reverses_stock_and_money(self):
        with_stock = self.milk.current_stock
        self.assertEqual(with_stock, self.stock_start + 100)
        inventory_services.delete_purchase(self.p.pk)
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.current_stock, self.stock_start)
        self.assertEqual(ledger.balance(CashAccount.BANK), 0)
        # سطر خرید برگشت می‌خورد، ولی تجدید ارزشِ ۱۰۰۰ml اولیه سر جایش است:
        # ۱۰۰۰ × ۲۵۰. یعنی موجودیِ اولیه هم با قیمت واقعی در دفتر نشسته.
        self.assertEqual(ledger.balance(LedgerAccount.INVENTORY), 250000)
        self.assertTrue(ledger.health()["ok"])

    def test_purchase_is_not_an_expense(self):
        """خرید تا وقتی مصرف نشده، هزینه نیست (اصل ۱ پروژه)."""
        pl = ledger.profit_and_loss()
        self.assertEqual(pl["expenses"], 0)
        self.assertEqual(pl["cogs"], 0)
        self.assertEqual(pl["net_profit"], 0)
        # ارزش انبار = موجودی فیزیکی × قیمت. موجودی اولیه‌ی ۱۰۰۰ml هم که
        # `unit_cost=0` ساخته شده بود، با اولین قیمتِ خرید تجدید ارزش شد.
        self.assertEqual(self.milk.current_stock, self.stock_start + 100)
        self.assertEqual(ledger.balance(LedgerAccount.INVENTORY), 275000)

    def test_first_price_revalues_stock_that_was_there_before(self):
        """کالای موجودِ بی‌قیمت با اولین خرید، به همان قیمت در دفتر می‌نشیند.

        رگرسیون: قبلاً موجودی قدیمی بی‌سند می‌ماند و گزارش انبار (۳۷۵٬۰۰۰) با
        گزارش اصلی (۱۲۵٬۰۰۰) نمی‌خواند.
        """
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.unit_cost, 250)
        self.assertEqual(
            ledger.balance(LedgerAccount.INVENTORY),
            int(self.milk.current_stock * self.milk.unit_cost),
        )
        self.assertTrue(ledger.health()["ok"], ledger.health()["problems"])

    def test_duplicate_order_does_not_double_consume(self):
        """همان درخواست دوباره، سفارش و مصرف دوم نمی‌سازد (اصل ۱۰)."""
        key = "dup-key-1"
        first = order_services.create_order(
            table_id=self.w.t5.id, source="admin",
            items=[{"product_id": self.w.latte.id, "quantity": 1}],
            idempotency_key=key,
        )
        second = order_services.create_order(
            table_id=self.w.t5.id, source="admin",
            items=[{"product_id": self.w.latte.id, "quantity": 1}],
            idempotency_key=key,
        )
        self.assertEqual(first.pk, second.pk)


class WasteTests(TestCase):
    def setUp(self):
        self.w = make_world()
        inventory_services.update_item(
            self.w.milk.id, {"unit_cost": "250"}
        )
        self.w.milk.refresh_from_db()
        self.stock_start = self.w.milk.current_stock

    def test_waste_lowers_stock_and_shows_as_a_loss(self):
        w = inventory_services.create_waste(
            {"item_id": self.w.milk.id, "quantity": "100", "reason": "spoiled"}
        )
        self.w.milk.refresh_from_db()
        self.assertEqual(self.w.milk.current_stock, self.stock_start - 100)
        pl = ledger.profit_and_loss()
        self.assertEqual(pl["waste"], 25000)
        # ضایعات پول جابه‌جا نمی‌کند؛ پولش را موقع خرید داده‌ایم.
        self.assertEqual(pl["expenses"], 0)
        self.assertEqual(ledger.balance(CashAccount.CASH), 0)

    def test_waste_delete_restores_stock_and_removes_loss(self):
        w = inventory_services.create_waste(
            {"item_id": self.w.milk.id, "quantity": "50", "reason": "expired"}
        )
        inventory_services.delete_waste(w.pk)
        self.w.milk.refresh_from_db()
        self.assertEqual(self.w.milk.current_stock, self.stock_start)
        self.assertEqual(ledger.profit_and_loss()["waste"], 0)

    def test_waste_beyond_stock_is_rejected(self):
        with self.assertRaises(OutOfStock):
            inventory_services.create_waste(
                {"item_id": self.w.milk.id, "quantity": "99999",
                 "reason": "expired"}
            )
        self.assertEqual(ledger.profit_and_loss()["waste"], 0)

    def test_invalid_reason_is_rejected(self):
        with self.assertRaises(Invalid):
            inventory_services.create_waste(
                {"item_id": self.w.milk.id, "quantity": "1", "reason": "because"}
            )

    def test_opening_stock_is_posted_so_ledger_stays_healthy(self):
        """موجودی اولیه‌ی کالا باید در دفتر هم ثبت شود."""
        inventory_services.create_item(
            {"name": "سیب", "unit": "piece", "unit_cost": "10000",
             "min_stock": "1", "initial_stock": "5"}
        )
        health = ledger.health()
        self.assertTrue(health["ok"], health["problems"])
        from inventory.models import InventoryItem

        apple = InventoryItem.objects.get(name="سیب")
        self.assertEqual(apple.current_stock, Decimal("5"))
        # موجودی اولیه هزینه نیست و پول هم جابه‌جا نمی‌کند
        self.assertEqual(ledger.profit_and_loss()["expenses"], 0)
        self.assertEqual(ledger.balance(CashAccount.CASH), 0)