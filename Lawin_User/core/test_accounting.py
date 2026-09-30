import json
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from core.jalali import to_jalali
from core.models import Expense
from core.testing import make_world
from orders import services


class AccountingReportTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.api = Client(headers={"Authorization": f"Token {self.w.token}"})
        o = services.create_order(
            table_id=self.w.t2.id,
            source="admin",
            items=[{"product_id": self.w.cake.id, "quantity": 2}],
        )
        services.pay_order(o.id, "cash", self.w.admin)  # ۲۲۰٬۰۰۰
        Expense.objects.create(
            title="خرید شیر",
            amount=50000,
            category="raw_materials",
            date=timezone.now(),
        )
        Expense.objects.create(
            title="اجاره",
            amount=900000,
            category="rent",
            date=timezone.now() - timedelta(days=40),
        )

    def test_requires_token(self):
        self.assertEqual(
            Client().get("/api/v1/accounting/transactions/").status_code, 401
        )
        self.assertEqual(Client().get("/api/v1/reports/").status_code, 401)

    def test_jalali(self):
        self.assertEqual(to_jalali(2026, 9, 23), (1405, 7, 1))
        self.assertEqual(to_jalali(2026, 9, 24), (1405, 7, 2))

    def test_transactions_ledger(self):
        rows = self.api.get("/api/v1/accounting/transactions/").json()
        self.assertEqual(len(rows), 3)
        income = [r for r in rows if r["type"] == "income"]
        self.assertEqual(len(income), 1)
        self.assertEqual(income[0]["amount"], 220000)
        self.assertEqual(income[0]["subtitle"], "میز 2 • نقدی")
        self.assertTrue(income[0]["id"].startswith("income-"))
        dates = [r["date"] for r in rows]
        self.assertEqual(dates, sorted(dates, reverse=True))
        exp = [r for r in rows if r["title"] == "خرید شیر"][0]
        self.assertEqual((exp["type"], exp["subtitle"]), ("expense", "خرید مواد اولیه"))

    def test_report_today(self):
        r = self.api.get("/api/v1/reports/?period=today").json()
        self.assertEqual((r["total_sales"], r["total_expenses"]), (220000, 50000))
        self.assertEqual((r["order_count"], r["items_sold_count"]), (1, 2))
        self.assertEqual(r["top_products"][0]["product_name"], "کیک")
        self.assertEqual(r["top_products"][0]["revenue"], 220000)
        self.assertEqual(
            r["expenses_by_category"], [{"category": "raw_materials", "amount": 50000}]
        )
        self.assertEqual(len(r["daily_points"]), 1)
        self.assertEqual(r["daily_points"][0]["sales"], 220000)

    def test_report_week_month_and_old_expense(self):
        for p in ("week", "month"):
            r = self.api.get(f"/api/v1/reports/?period={p}").json()
            self.assertEqual(r["total_sales"], 220000)
            self.assertNotIn(
                900000, [c["amount"] for c in r["expenses_by_category"]]
            )  # ۴۰ روز قبل
        month = self.api.get("/api/v1/reports/?period=month").json()
        self.assertLessEqual(len(month["daily_points"]), 31)

    def test_report_custom(self):
        a = (timezone.localdate() - timedelta(days=60)).isoformat()
        b = timezone.localdate().isoformat()
        r = self.api.get(f"/api/v1/reports/?period=custom&start={a}&end={b}").json()
        self.assertEqual(r["total_expenses"], 950000)
        self.assertEqual(len(r["daily_points"]), 61)  # هر دو سر بازه شامل است

    def test_custom_validation(self):
        for qs in (
            "period=custom",
            "period=custom&start=2026-01-10&end=2026-01-01",
            "period=custom&start=2020-01-01&end=2026-01-01",
            "period=yearly",
        ):
            r = self.api.get(f"/api/v1/reports/?{qs}")
            self.assertEqual(r.status_code, 400, qs)
            self.assertEqual(r.json()["error"]["code"], "validation")
