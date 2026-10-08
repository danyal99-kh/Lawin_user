"""تست‌های نسیه (Accounts Receivable).

اینجا «قانون‌های مالیِ» نسیه شکسته می‌شوند اگر پیاده‌سازی غلط باشد:
درآمد لحظه‌ی ثبت، پول لحظه‌ی وصول، مانده‌ی بدهکاران همیشه = جمع مانده‌ی
نسیه‌ها، بیش‌پرداخت رد، و برگشت سفارش باید همه‌چیز را دقیقاً به صفر برگرداند.
"""

import json

from django.test import Client, TestCase

from core import ledger
from core.models import CashAccount, JournalKind, LedgerAccount
from core.security import TICKET_HEADER
from core.testing import make_financial_world
from credits import services
from credits.models import Credit, CreditPayment, Debtor
from orders import services as order_services
from orders.models import Payment


def post(c, url, body):
    return c.post(url, json.dumps(body), content_type="application/json")


class CreditBase(TestCase):
    def setUp(self):
        self.w = make_financial_world()
        self.api = Client(
            headers={
                "Authorization": f"Token {self.w.token}",
                TICKET_HEADER: self.w.security_ticket,
            }
        )

    def make_order(self, table, qty=2):
        return order_services.create_order(
            table_id=table.id, source="admin",
            items=[{"product_id": self.w.cake.id, "quantity": qty}],
        )  # کیک بدون Recipe ⇒ بهای تمام‌شده صفر و برگشت ساده است

    def pay_credit(self, order, name="علی رضا"):
        return order_services.pay_order(
            order.id, "credit", self.w.admin, debtor_name=name
        )

    def receivable(self):
        return ledger.balance(LedgerAccount.RECEIVABLE)

    def cash(self):
        return ledger.balance(CashAccount.CASH)


class CreditSaleTests(CreditBase):
    def test_credit_sale_creates_receivable_not_cash(self):
        """نسیه باید درآمد بسازد ولی پولی وارد صندوق نکند."""
        o = self.make_order(self.w.t2, qty=2)  # ۲۲۰٬۰۰۰
        self.pay_credit(o)
        o.refresh_from_db()

        self.assertEqual((o.status, o.payment_status, o.payment_method),
                         ("paid", "credit", "credit"))
        self.assertEqual(Payment.objects.get(order=o).method, "credit")
        c = Credit.objects.get(order=o)
        self.assertEqual(
            (c.amount, c.remaining_amount, c.status, c.debtor.name),
            (220000, 220000, Credit.Status.OPEN, "علی رضا"),
        )
        # اصل: درآمد همان لحظه، پول فقط لحظه‌ی تسویه.
        self.assertEqual(ledger.profit_and_loss()["revenue"], 220000)
        self.assertEqual(self.receivable(), 220000)
        self.assertEqual(self.cash(), 0)

    def test_credit_requires_debtor_name(self):
        o = self.make_order(self.w.t2)
        with self.assertRaisesMessage(Exception, "نام بدهکار"):
            order_services.pay_order(o.id, "credit", self.w.admin)
        o.refresh_from_db()
        self.assertEqual(o.payment_status, "unpaid")
        self.assertEqual(Credit.objects.count(), 0)
        # حتی با پرداخت چندروشی، اگر سهمی نسیه باشد نام لازم است.
        o2 = self.make_order(self.w.t5, qty=1)
        with self.assertRaisesMessage(Exception, "نام بدهکار"):
            order_services.pay_order(
                o2.id, None, self.w.admin,
                payments=[{"method": "cash", "amount": 50000},
                          {"method": "credit", "amount": 60000}],
            )
        self.assertEqual(Credit.objects.count(), 0)

    def test_debtor_is_reused_not_duplicated(self):
        """تایپِ دوباره‌ی یک نام باید همان بدهکار قبلی را پیدا کند."""
        a = self.make_order(self.w.t2, qty=1)
        b = self.make_order(self.w.t5, qty=1)
        self.pay_credit(a, name="  علی   رضا ")
        self.pay_credit(b, name="علی رضا")
        self.assertEqual(Debtor.objects.count(), 1)
        self.assertEqual(Debtor.objects.get().name, "علی رضا")
        self.assertEqual(Credit.objects.count(), 2)

    def test_mixed_cash_and_credit(self):
        """پرداخت ترکیبی: سهم نقدی همان لحظه، سهم نسیه به حساب طلب."""
        o = self.make_order(self.w.t2, qty=2)
        order_services.pay_order(
            o.id, None, self.w.admin, debtor_name="مشتری",
            payments=[{"method": "cash", "amount": 100000},
                      {"method": "credit", "amount": 120000}],
        )
        self.assertEqual(Payment.objects.filter(order=o).count(), 2)
        self.assertEqual(Credit.objects.get(order=o).amount, 120000)
        self.assertEqual(self.cash(), 100000)
        self.assertEqual(self.receivable(), 120000)
        self.assertEqual(ledger.profit_and_loss()["revenue"], 220000)
        self.assertTrue(ledger.health()["ok"], ledger.health()["problems"])

    def test_pay_table_with_debtor_name(self):
        """پرداخت نسیه‌ای از مسیر میز هم باید بدهکار بسازد."""
        self.make_order(self.w.t2, qty=2)
        r = post(self.api, f"/api/v1/tables/{self.w.t2.id}/pay/",
                 {"method": "credit", "debtor_name": "سالن"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Credit.objects.count(), 1)
        self.assertEqual(self.receivable(), 220000)

        self.make_order(self.w.t5, qty=1)
        r = post(self.api, f"/api/v1/tables/{self.w.t5.id}/pay/",
                 {"method": "credit"})
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(Credit.objects.count(), 1)


class SettlementTests(CreditBase):
    def setUp(self):
        super().setUp()
        self.a = self.make_order(self.w.t2, qty=2)  # ۲۲۰٬۰۰۰
        self.b = self.make_order(self.w.t5, qty=1)  # ۱۱۰٬۰۰۰
        self.pay_credit(self.a, "مشتری")
        self.pay_credit(self.b, "مشتری")

    def test_settlement_is_oldest_first(self):
        """۲۵۰٬۰۰۰ باید اول کل نسیه‌ی قدیمی‌تر و بعد بخشی از بعدی را ببندد."""
        result = services.settle(amount=250000, debtor_id=Debtor.objects.get().id,
                                 user=self.w.admin)
        self.assertEqual(result["total"], 250000)
        a, b = Credit.objects.get(order=self.a), Credit.objects.get(order=self.b)
        self.assertEqual((a.remaining_amount, a.status), (0, Credit.Status.SETTLED))
        self.assertEqual((b.remaining_amount, b.status), (80000, Credit.Status.OPEN))
        self.assertEqual(self.cash(), 250000)
        self.assertEqual(self.receivable(), 80000)
        self.assertEqual(sum(c.remaining_amount for c in Credit.objects.all()),
                         self.receivable())
        self.assertTrue(ledger.health()["ok"], ledger.health()["problems"])

    def test_overpayment_is_rejected(self):
        """بیش از مانده‌ی باز نمی‌توان وصول کرد؛ هیچ چیزی هم ثبت نمی‌شود."""
        with self.assertRaisesMessage(Exception, "بیشتر است"):
            services.settle(amount=330001, debtor_id=Debtor.objects.get().id,
                            user=self.w.admin)
        self.assertEqual(CreditPayment.objects.count(), 0)
        self.assertEqual(self.cash(), 0)
        self.assertEqual(self.receivable(), 330000)

    def test_settlement_can_be_restricted_to_one_credit(self):
        """با credit_id فقط همان نسیه تسویه می‌شود، حتی اگر قدیمی‌تر نباشد."""
        b = Credit.objects.get(order=self.b)
        services.settle(amount=110000, credit_id=b.id, user=self.w.admin)
        b.refresh_from_db()
        a = Credit.objects.get(order=self.a)
        self.assertEqual((a.remaining_amount, a.status), (220000, Credit.Status.OPEN))
        self.assertEqual((b.remaining_amount, b.status), (0, Credit.Status.SETTLED))
        self.assertEqual(self.receivable(), 220000)

    def test_settlement_is_idempotent(self):
        """درخواست تکراری با همان idempotency_key پول را دوبار نمی‌گیرد."""
        key = "settle-1"
        services.settle(amount=50000, debtor_id=Debtor.objects.get().id,
                        user=self.w.admin, idempotency_key=key)
        services.settle(amount=50000, debtor_id=Debtor.objects.get().id,
                        user=self.w.admin, idempotency_key=key)
        self.assertEqual(CreditPayment.objects.count(), 1)
        self.assertEqual(self.cash(), 50000)
        self.assertEqual(self.receivable(), 330000 - 50000)

    def test_settlement_needs_an_open_credit(self):
        services.settle(amount=1, credit_id=Credit.objects.first().id,
                        user=self.w.admin, idempotency_key="x")
        services.settle(amount=329999, debtor_id=Debtor.objects.get().id,
                        user=self.w.admin, idempotency_key="y")
        with self.assertRaisesMessage(Exception, "وجود ندارد"):
            services.settle(amount=1, debtor_id=Debtor.objects.get().id,
                            user=self.w.admin)

    def test_zero_and_negative_amount_rejected(self):
        for bad in (0, -5, "abc", True, None):
            with self.assertRaises(Exception):
                services.settle(amount=bad, debtor_id=Debtor.objects.get().id)

    def test_api_endpoint_and_permissions(self):
        self.assertEqual(
            Client().post("/api/v1/credits/payments/", "{}",
                          content_type="application/json").status_code, 401
        )
        no_ticket = Client(headers={"Authorization": f"Token {self.w.token}"})
        # به درخواست کاربر، بخش نسیه رمز امنیتی دوم نمی‌خواهد؛ فقط ادمینِ
        # واردشده کافی است (برخلاف حسابداری/هزینه‌ها که بلیت نیز لازم دارند).
        # بدنه‌ی خالی یعنی validation (400)، نه 403.
        r = no_ticket.post("/api/v1/credits/payments/", "{}",
                           content_type="application/json")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()["error"]["code"], "validation")
        debtor = Debtor.objects.get()
        r = post(self.api, "/api/v1/credits/payments/",
                 {"debtor_id": debtor.id, "amount": 100000, "account": "bank"})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["total"], 100000)
        self.assertEqual(self.cash(), 0)
        self.assertEqual(ledger.balance(CashAccount.BANK), 100000)
        self.assertEqual(Credit.objects.get(order=self.a).remaining_amount, 120000)

    def test_bad_account_rejected(self):
        with self.assertRaisesMessage(Exception, "حساب پرداخت"):
            services.settle(amount=1000, debtor_id=Debtor.objects.get().id,
                            account="gold")


class RefundTests(CreditBase):
    def test_refund_of_unsettled_credit_zeroes_everything(self):
        o = self.make_order(self.w.t2, qty=2)
        self.pay_credit(o)
        order_services.refund_order(o.id, self.w.admin)

        c = Credit.objects.get(order=o)
        self.assertEqual((c.status, c.remaining_amount),
                         (Credit.Status.REFUNDED, 0))
        self.assertEqual(self.receivable(), 0)
        self.assertEqual(self.cash(), 0)
        self.assertEqual(ledger.profit_and_loss()["revenue"], 0)
        self.assertTrue(ledger.health()["ok"], ledger.health()["problems"])

    def test_refund_after_partial_settlement_returns_the_money(self):
        """پولِ وصول‌شده باید از همان حسابی که آمده برگردد؛ وگرنه دفتر منفی می‌شد."""
        o = self.make_order(self.w.t2, qty=2)
        self.pay_credit(o)
        credit = Credit.objects.get(order=o)
        services.settle(amount=100000, credit_id=credit.id, user=self.w.admin)
        self.assertEqual((self.cash(), self.receivable()), (100000, 120000))

        order_services.refund_order(o.id, self.w.admin)
        credit.refresh_from_db()
        self.assertEqual((credit.status, credit.remaining_amount),
                         (Credit.Status.REFUNDED, 0))
        self.assertEqual((self.cash(), self.receivable()), (0, 0))
        self.assertEqual(ledger.profit_and_loss()["revenue"], 0)
        health = ledger.health()
        self.assertTrue(health["ok"], health["problems"])

    def test_refund_does_not_touch_other_debtors(self):
        a = self.make_order(self.w.t2, qty=2)
        b = self.make_order(self.w.t5, qty=1)
        self.pay_credit(a, "اولی")
        self.pay_credit(b, "دومی")
        order_services.refund_order(a.id, self.w.admin)
        self.assertEqual(self.receivable(), 110000)
        self.assertEqual(
            Credit.objects.get(order=b).remaining_amount, 110000
        )
        self.assertTrue(ledger.health()["ok"], ledger.health()["problems"])


class CreditReportTests(CreditBase):
    def test_report_and_dashboard_expose_receivables(self):
        o = self.make_order(self.w.t2, qty=2)
        self.pay_credit(o)
        r = self.api.get("/api/v1/reports/?period=month").json()
        self.assertEqual(r["credit_sales"], 220000)
        self.assertEqual(r["outstanding_receivables"], 220000)
        self.assertEqual(r["cash_received"], 0)
        self.assertEqual(r["credit_collections"], 0)

        summary = self.api.get("/api/v1/dashboard/summary/").json()
        self.assertEqual(summary["receivables_balance"], 220000)

        services.settle(amount=70000, credit_id=Credit.objects.get().id,
                        user=self.w.admin)
        r = self.api.get("/api/v1/reports/?period=month").json()
        self.assertEqual(r["credit_collections"], 70000)
        self.assertEqual(r["outstanding_receivables"], 150000)
        self.assertEqual(r["cash_received"], 70000)

    def test_transactions_endpoint_marks_credit_kinds(self):
        o = self.make_order(self.w.t2, qty=2)
        self.pay_credit(o)
        services.settle(amount=50000, credit_id=Credit.objects.get().id,
                        user=self.w.admin)
        rows = self.api.get("/api/v1/accounting/transactions/").json()
        by_kind = {r["kind"]: r for r in rows}
        self.assertEqual(by_kind["sale"]["type"], "income")
        self.assertIn("نسیه", by_kind["sale"]["subtitle"])
        self.assertEqual(by_kind["credit_payment"]["type"], "income")
        self.assertEqual(by_kind["credit_payment"]["amount"], 50000)

    def test_payment_methods_report_includes_credit(self):
        o = self.make_order(self.w.t2, qty=2)
        self.pay_credit(o)
        r = self.api.get("/api/v1/reports/payment-methods/?period=month").json()
        rows = {m["method"]: m for m in r["methods"]}
        self.assertEqual(rows["credit"]["amount"], 220000)
        self.assertEqual(rows["credit"]["label"], "نسیه")


class DebtorApiTests(CreditBase):
    def test_list_and_search_debtors(self):
        for table, name in ((self.w.t2, "اول"), (self.w.t5, "دوم")):
            o = self.make_order(table, qty=1)
            self.pay_credit(o, name)
        r = self.api.get("/api/v1/credits/debtors/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()), 2)
        self.assertTrue(all(d["debt"] == 110000 for d in r.json()))

        r = self.api.get("/api/v1/credits/debtors/?q=دوم")
        self.assertEqual([d["name"] for d in r.json()], ["دوم"])

    def test_debtor_detail_lists_credits(self):
        o = self.make_order(self.w.t2, qty=2)
        self.pay_credit(o)
        debtor = Debtor.objects.get()
        r = self.api.get(f"/api/v1/credits/debtors/{debtor.id}/")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["debtor"]["name"], "علی رضا")
        self.assertEqual(len(body["credits"]), 1)
        self.assertEqual(body["credits"][0]["amount"], 220000)
        self.assertEqual(
            self.api.get("/api/v1/credits/debtors/9999/").status_code, 404
        )

    def test_credits_list_filters(self):
        a = self.make_order(self.w.t2, qty=2)
        self.pay_credit(a)
        r = self.api.get("/api/v1/credits/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()), 1)
        self.assertEqual(self.api.get(
            "/api/v1/credits/?status=settled").json(), [])
        self.assertEqual(self.api.get(
            "/api/v1/credits/?status=nope").status_code, 400)
        self.assertEqual(self.api.get(
            "/api/v1/credits/?debtor=abc").status_code, 400)
