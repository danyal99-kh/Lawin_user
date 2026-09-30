from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import Client, TestCase
from django.utils import timezone

from core.jalali import gregorian_to_jalali, start_of_jalali_month
from core.models import Expense
from core.testing import make_world
from orders import services

TEHRAN = ZoneInfo("Asia/Tehran")
URL = "/api/v1/dashboard/summary/"


class JalaliTests(TestCase):
    def test_conversion_and_month_start(self):
        self.assertEqual(gregorian_to_jalali(2026, 9, 23), (1405, 7, 1))  # ۱ مهر ۱۴۰۵
        self.assertEqual(gregorian_to_jalali(2026, 9, 30), (1405, 7, 8))
        got = start_of_jalali_month(datetime(2026, 9, 30, 15, 0, tzinfo=TEHRAN))
        self.assertEqual(got, datetime(2026, 9, 23, 0, 0, tzinfo=TEHRAN))


class DashboardTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.api = Client(headers={"Authorization": f"Token {self.w.token}"})

    def _order(self, table, product, qty=1):
        return services.create_order(
            table_id=table.id,
            source="admin",
            items=[{"product_id": product.id, "quantity": qty}],
        )

    def test_requires_token(self):
        self.assertEqual(Client().get(URL).status_code, 401)

    def test_empty_shape(self):
        d = self.api.get(URL).json()
        self.assertEqual(
            (
                d["today_sales"],
                d["month_sales"],
                d["today_expenses"],
                d["month_expenses"],
                d["today_order_count"],
            ),
            (0, 0, 0, 0, 0),
        )
        self.assertEqual(len(d["tables"]), 3)
        self.assertEqual(
            (d["low_stock_items"], d["recent_orders"], d["recent_expenses"]),
            ([], [], []),
        )

    def test_sales_orders_and_cancelled(self):
        paid = self._order(self.w.t2, self.w.cake, 2)  # ۲۲۰٬۰۰۰
        services.pay_order(paid.id, "cash", self.w.admin)
        self._order(self.w.t5, self.w.cake)  # باز، پرداخت‌نشده
        gone = self._order(self.w.t8, self.w.cake)
        services.change_status(gone.id, "cancelled")
        d = self.api.get(URL).json()
        self.assertEqual(d["today_sales"], 220000)
        self.assertEqual(d["month_sales"], 220000)
        self.assertEqual(d["today_order_count"], 2)  # لغوشده حساب نمی‌شود
        self.assertEqual(len(d["recent_orders"]), 3)
        self.assertEqual(d["recent_orders"][0]["number"], gone.number)  # جدیدترین اول
        self.assertIsInstance(d["recent_orders"][0]["table"]["number"], int)

    def test_expenses_today_vs_month(self):
        now = timezone.now()
        Expense.objects.create(
            title="شیر", amount=850000, category="raw_materials", date=now
        )
        Expense.objects.create(
            title="قدیمی", amount=999, category="other", date=now - timedelta(days=40)
        )
        d = self.api.get(URL).json()
        self.assertEqual((d["today_expenses"], d["month_expenses"]), (850000, 850000))
        self.assertEqual([e["title"] for e in d["recent_expenses"]], ["شیر", "قدیمی"])
        self.assertIsNone(d["recent_expenses"][0]["note"])  # note خالی → null

    def test_low_stock(self):
        self.w.milk.current_stock = Decimal(50)  # min=100
        self.w.milk.save()
        d = self.api.get(URL).json()
        self.assertEqual([i["name"] for i in d["low_stock_items"]], ["شیر"])
        item = d["low_stock_items"][0]
        self.assertEqual(
            (item["unit"], item["current_stock"], item["min_stock"]),
            ("ml", 50.0, 100.0),
        )
        self.assertIsInstance(item["unit_cost"], float)
