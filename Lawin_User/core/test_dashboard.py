"""تست‌های داشبورد: GET /api/v1/dashboard/summary/

همه‌ی محاسبه‌ها با «زمان تهران» سنجیده می‌شوند؛ برای مرزهای روز و ماه شمسی
زمانِ سرور قفل (patch) می‌شود تا تست مستقل از ساعت اجرا باشد.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.authtoken.models import Token

from core.jalali import jalali_day, start_of_day, start_of_jalali_month, to_jalali
from core.models import Expense
from core.testing import make_world
from inventory.models import InventoryItem
from orders import services
from orders.models import Order
from tables.models import Table

TEHRAN = ZoneInfo("Asia/Tehran")
URL = "/api/v1/dashboard/summary/"

# ۹ مهر ۱۴۰۵، ۱۲:۰۰ تهران  (معادل 2026-10-01 08:30 UTC)
# روز تهران از 2026-10-01 00:00 و ماه شمسی از 2026-09-23 00:00 شروع می‌شود.
FROZEN = datetime(2026, 10, 1, 12, 0, tzinfo=TEHRAN)
DAY_START = datetime(2026, 10, 1, 0, 0, tzinfo=TEHRAN)
DAY_END = datetime(2026, 10, 2, 0, 0, tzinfo=TEHRAN)
MONTH_START = datetime(2026, 9, 23, 0, 0, tzinfo=TEHRAN)


def T(y, m, d, H=0, M=0):
    """یک datetime آگاه به وقت تهران."""
    return datetime(y, m, d, H, M, tzinfo=TEHRAN)


class JalaliTests(TestCase):
    def test_conversion_and_month_start(self):
        self.assertEqual(to_jalali(2026, 9, 23), (1405, 7, 1))  # ۱ مهر ۱۴۰۵
        self.assertEqual(to_jalali(2026, 9, 30), (1405, 7, 8))
        got = start_of_jalali_month(datetime(2026, 9, 30, 15, 0, tzinfo=TEHRAN))
        self.assertEqual(got, datetime(2026, 9, 23, 0, 0, tzinfo=TEHRAN))

    def test_month_start_covers_every_month_length(self):
        """اول ماه شمسی = اول همان ماه میلادی، حتی وقتی ماه ۶ با ۳۱ روز شروع شود."""
        for g_day in (23, 24, 30):  # ۱، ۲ و ۸ مهر
            self.assertEqual(
                start_of_jalali_month(T(2026, 9, g_day, 5, 0)), MONTH_START
            )
        self.assertEqual(jalali_day(MONTH_START.date()), 1)

    def test_start_of_day_is_tehran_not_utc(self):
        """۰۰:۰۰ تهران = ۲۰:۳۰ UTC روز قبل."""
        got = start_of_day(datetime(2026, 10, 1, 8, 30, tzinfo=TEHRAN))
        self.assertEqual(got, DAY_START)
        self.assertEqual(got.utcoffset(), timedelta(hours=3, minutes=30))
        self.assertEqual(got.astimezone(ZoneInfo("UTC")).hour, 20)


class DashboardTestBase(TestCase):
    def setUp(self):
        # کل تست روی زمانِ قفل‌شده اجرا می‌شود تا «امروز» برای داشبورد قطعی باشد و
        # نتیجه به ساعت اجرای واقعی وابسته نباشد. `_pay`/`_moved` برای لحظه‌های
        # دیگر دوباره زمان را قفل می‌کنند.
        self._clock = patch("django.utils.timezone.now", return_value=FROZEN)
        self._clock.start()
        self.addCleanup(self._clock.stop)
        self.w = make_world()
        self.api = Client(headers={"Authorization": f"Token {self.w.token}"})

    def _order(self, table=None, product=None, qty=1):
        return services.create_order(
            table_id=(table or self.w.t2).id,
            source="admin",
            items=[{"product_id": (product or self.w.cake).id, "quantity": qty}],
        )

    def _paid(self, moment, qty=1):
        """سفارشی که در لحظه‌ی دلخواهِ تهران پرداخت شده است.

        زمان باید هنگامِ خودِ `pay_order` قفل شود، چون دفتر حسابداری همان لحظه
        `occurred_at` را ثبت می‌کند. عوض‌کردن `paid_at` بعد از پرداخت (که قبلاً
        با `.update()` انجام می‌شد) زمانِ واقعی فروش را از دفتر جدا می‌کرد و
        دقیقاً همان ناهماهنگی‌ای است که این بازطراحی برای جلوگیری از آن است.
        """
        o = self._order(qty=qty)
        self._pay(o.id, moment)
        return o

    def _pay(self, order_id, moment, method="cash"):
        """پرداخت در زمانِ قفل‌شده تا زمانِ ثبت‌شده در دفتر قطعی باشد."""
        with patch("django.utils.timezone.now", return_value=moment):
            return services.pay_order(order_id, method, self.w.admin)

    def _moved(self, order, moment):
        Order.objects.filter(pk=order.pk).update(created_at=moment)
        return order

    def _expense(self, title, amount, moment, category="other"):
        return Expense.objects.create(
            title=title, amount=amount, category=category, date=moment
        )

    def _summary(self, now=FROZEN):
        """درخواست داشبورد با «اکنون» قفل‌شده تا مرزهای روز/ماه قابل آزمون باشد."""
        with patch("django.utils.timezone.now", return_value=now):
            return self.api.get(URL).json()


class DashboardShapeTests(DashboardTestBase):
    def test_requires_token(self):
        self.assertEqual(Client().get(URL).status_code, 401)

    def test_non_staff_token_is_forbidden(self):
        user = get_user_model().objects.create_user("cashier", password="x")
        token = Token.objects.create(user=user).key
        res = Client(headers={"Authorization": f"Token {token}"}).get(URL)
        self.assertEqual(res.status_code, 403)

    def test_empty_shape(self):
        d = self._summary()
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

    def test_period_is_tehran_jalali(self):
        p = self._summary()["period"]
        self.assertEqual(p["calendar"], "jalali")
        self.assertEqual((p["year"], p["month"], p["day"]), (1405, 7, 9))
        self.assertEqual(p["day_start"], DAY_START.isoformat())
        self.assertEqual(p["day_end"], DAY_END.isoformat())
        self.assertEqual(p["month_start"], MONTH_START.isoformat())

    def test_tables_shape(self):
        rows = self._summary()["tables"]
        self.assertEqual([t["number"] for t in rows], [2, 5, 8])
        for t in rows:
            self.assertEqual(sorted(t), ["id", "number", "status"])
            self.assertEqual(t["status"], Table.Status.EMPTY)

    def test_query_count_is_bounded(self):
        """جمع‌ها در DB محاسبه می‌شوند، نه در پایتون؛ و تعداد کوئری کنترل‌شده است.

        قبلاً ۴ کوئری `Sum` جدا برای فروش/هزینه‌ی امروز و ماه لازم بود؛ حالا هر سه
        بازه (مانده‌ی کل، امروز، این ماه) در **یک** کوئری `balances_multi` از دفتر
        حسابداری می‌آید. یک کوئری هم به `payments` اضافه شد چون هر سفارش حالا
        فهرست پرداخت‌هایش را هم برمی‌گرداند.
        """
        self._order()
        with CaptureQueriesContext(connection) as ctx:
            self._summary()
        # ۱ احراز هویت + ۱ دفتر (هر سه بازه) + ۱ تنظیمات + ۱ شمارش سفارش امروز
        # + ۱ کالای رو به اتمام + ۱ میز + ۱ سفارش‌های اخیر + ۱ اقلام سفارش
        # + ۱ پرداخت‌های سفارش + ۱ هزینه‌های اخیر
        self.assertEqual(len(ctx.captured_queries), 10)


class DashboardSalesTests(DashboardTestBase):
    def test_sales_orders_and_cancelled(self):
        paid = self._order(self.w.t2, self.w.cake, 2)  # ۲۲۰٬۰۰۰
        self._pay(paid.id, FROZEN)
        self._order(self.w.t5, self.w.cake)  # باز، پرداخت‌نشده
        gone = self._order(self.w.t8, self.w.cake)
        services.change_status(gone.id, "cancelled")
        d = self._summary()
        self.assertEqual(d["today_sales"], 220000)
        self.assertEqual(d["month_sales"], 220000)
        self.assertEqual(d["today_order_count"], 2)  # لغوشده حساب نمی‌شود
        self.assertEqual(len(d["recent_orders"]), 3)
        self.assertEqual(d["recent_orders"][0]["number"], gone.number)  # جدیدترین اول
        self.assertIsInstance(d["recent_orders"][0]["table"]["number"], int)

    def test_only_paid_orders_count_as_sales(self):
        """سفارش باز و سفارش لغوشده هیچ‌کدام فروش نیستند."""
        paid = self._paid(T(2026, 10, 1, 9, 0))
        open_ = self._order()
        gone = self._order()
        services.change_status(gone.id, "cancelled")
        d = self._summary()
        self.assertEqual(d["today_sales"], paid.total)
        self.assertEqual(d["month_sales"], paid.total)
        self.assertEqual(
            d["today_order_count"], 2
        )  # دو سفارش امروز، یکی لغوشده

    def test_today_sales_uses_tehran_day(self):
        """۰۰:۰۰ تهران داخل بازه است، یک ثانیه قبل از آن نه."""
        self._paid(DAY_START)
        d = self._summary()
        self.assertEqual(d["today_sales"], 110000)
        self.assertEqual(d["month_sales"], 110000)

        self._paid(DAY_START - timedelta(seconds=1))
        d = self._summary()
        self.assertEqual(d["today_sales"], 110000)  # دیروز تهران
        self.assertEqual(d["month_sales"], 220000)  # ولی هنوز در همین ماه شمسی

    def test_tehran_midnight_not_utc_day(self):
        """
        مرز حیاتی: FROZEN برابر 2026-10-01 08:30 UTC است، پس «روز UTC» از
        2026-10-01 03:30 تهران شروع می‌شود. سفارش ساعت ۰۱:۰۰ تهران
        (2026-09-30 21:30 UTC) برای داشبورد «امروز» است، ولی با منطق
        روزِ UTC دیروز به حساب می‌آمد.
        """
        self.assertEqual(
            FROZEN.astimezone(ZoneInfo("UTC")).date().isoformat(), "2026-10-01"
        )
        self._moved(self._order(), T(2026, 10, 1, 1, 0))  # امروز در تهران
        self._moved(self._order(), T(2026, 9, 30, 23, 0))  # دیروز در تهران
        d = self._summary()
        self.assertEqual(d["today_order_count"], 1)

    def test_day_range_is_half_open(self):
        """۰۰:۰۰ روز بعد، دیگر «امروز» نیست."""
        self._moved(self._order(), DAY_END)
        self.assertEqual(self._summary()["today_order_count"], 0)
        self._moved(self._order(), DAY_END - timedelta(seconds=1))
        self.assertEqual(self._summary()["today_order_count"], 1)

    def test_month_sales_follow_jalali_month(self):
        """
        ۲۲ سپتامبر = ۳۱ شهریور (پایان ماه شمسی) و ۲۳ سپتامبر = ۱ مهر
        (شروع ماه شمسی). هر دو در ماه میلادی سپتامبرند، پس ماه میلادی
        نمی‌تواند جواب درست بدهد.
        """
        last_shahrivar = self._paid(T(2026, 9, 22, 23, 0))
        first_mordad = self._paid(T(2026, 9, 23, 0, 1))
        self.assertEqual(to_jalali(2026, 9, 22), (1405, 6, 31))
        self.assertEqual(to_jalali(2026, 9, 23), (1405, 7, 1))
        self.assertEqual(last_shahrivar.total, first_mordad.total)  # هر دو ۱۱۰٬۰۰۰
        d = self._summary()
        self.assertEqual(d["month_sales"], first_mordad.total)  # فقط ۱ مهر
        self.assertEqual(d["today_sales"], 0)  # هر دو در روزهای گذشته‌اند

    def test_month_range_ends_with_today(self):
        """ماه جاری از اول ماه شمسی تا شروع روز بعد (نه تا پایان ماه)."""
        self._paid(T(2026, 9, 23, 0, 1))
        self._paid(DAY_END)  # فردای تهران: نه امروز، نه این ماه
        d = self._summary()
        self.assertEqual(d["today_sales"], 0)
        self.assertEqual(d["month_sales"], 110000)

    def test_sales_follow_payment_time_not_creation(self):
        """فروش بر اساس زمان پرداخت است، نه زمان ثبت سفارش."""
        o = self._order()
        self._pay(o.id, FROZEN)  # فروش «امروز»
        self._moved(o, T(2026, 8, 1, 10, 0))  # ولی سفارش مردادی است
        d = self._summary()
        self.assertEqual(d["today_sales"], o.total)
        self.assertEqual(d["today_order_count"], 0)  # سفارش دیروزی است


class DashboardOrderCountTests(DashboardTestBase):
    def test_cancelled_excluded_from_today_count(self):
        orders = [self._moved(self._order(), T(2026, 10, 1, 8, i)) for i in range(10)]
        for o in orders[:2]:
            services.change_status(o.id, "cancelled")
        self.assertEqual(self._summary()["today_order_count"], 8)
        self.assertEqual(Order.objects.filter(status="paid").count(), 0)


class DashboardRecentTests(DashboardTestBase):
    def test_recent_orders_exactly_six_newest_first(self):
        made = []
        for i in range(8):
            o = self._order()
            self._moved(o, T(2026, 10, 1, 6, i))
            made.append(o)
        rows = self._summary()["recent_orders"]
        self.assertEqual(len(rows), 6)
        self.assertEqual([r["number"] for r in rows], [o.number for o in made][::-1][:6])
        for r in rows:
            self.assertTrue(
                {"id", "number", "status", "total", "created_at", "table"} <= set(r)
            )
            self.assertEqual(len(r["items"]), 1)  # prefetch درست کار می‌کند
        self.assertTrue(
            datetime.fromisoformat(rows[0]["created_at"]) == T(2026, 10, 1, 6, 7)
        )
        self.assertRegex(rows[0]["created_at"], r"[+-]\d{2}:\d{2}$")  # offset دار است
        self.assertTrue(
            datetime.fromisoformat(rows[0]["created_at"]).tzinfo is not None
        )

    def test_recent_expenses_exactly_five_newest_first(self):
        for i in range(7):
            self._expense(f"هزینه {i}", 1000 * (i + 1), T(2026, 9, 30, 10, i))
        rows = self._summary()["recent_expenses"]
        self.assertEqual(len(rows), 5)
        self.assertEqual([r["title"] for r in rows], [f"هزینه {i}" for i in (6, 5, 4, 3, 2)])
        self.assertEqual(
            sorted(rows[0]),
            # `account` هم اضافه شده: هر هزینه از کدام حساب (صندوق/بانک) پرداخت شده.
            ["account", "amount", "category", "date", "id", "note", "title"],
        )
        self.assertEqual(
            datetime.fromisoformat(rows[0]["date"]), T(2026, 9, 30, 10, 6)
        )


class DashboardExpenseTests(DashboardTestBase):
    def test_expenses_today_vs_month(self):
        self._expense("شیر", 850000, T(2026, 10, 1, 7, 0))
        self._expense("قدیمی", 999, T(2026, 8, 20, 7, 0))
        d = self._summary()
        self.assertEqual((d["today_expenses"], d["month_expenses"]), (850000, 850000))
        self.assertEqual([e["title"] for e in d["recent_expenses"]], ["شیر", "قدیمی"])
        self.assertIsNone(d["recent_expenses"][0]["note"])  # note خالی → null

    def test_expense_day_boundary(self):
        self._expense("قبل از نیمه‌شب", 500, T(2026, 9, 30, 23, 59))
        self._expense("بعد از نیمه‌شب", 700, T(2026, 10, 1, 0, 0))
        d = self._summary()
        self.assertEqual(d["today_expenses"], 700)
        self.assertEqual(d["month_expenses"], 1200)  # هر دو داخل ماه شمسی جاری‌اند

    def test_expense_uses_tehran_day(self):
        """۰۰:۳۰ تهران = ۲۱:۰۰ UTC روز قبل؛ با روز UTC اشتباه می‌شد."""
        self._expense("نیمه‌شب تهران", 300, T(2026, 10, 1, 0, 30))
        self.assertEqual(self._summary()["today_expenses"], 300)


class DashboardLowStockTests(DashboardTestBase):
    def test_low_stock(self):
        self.w.milk.current_stock = Decimal(50)  # min=100
        self.w.milk.save()
        d = self._summary()
        self.assertEqual([i["name"] for i in d["low_stock_items"]], ["شیر"])
        item = d["low_stock_items"][0]
        self.assertEqual(
            (item["unit"], item["current_stock"], item["min_stock"]),
            ("ml", 50.0, 100.0),
        )
        self.assertIsInstance(item["unit_cost"], float)

    def test_threshold_is_inclusive_and_not_hardcoded(self):
        """آستانه از min_stock هر کالا می‌آید؛ برابرِ min_stock هم «کم» است."""
        InventoryItem.objects.create(
            name="برابر", unit="piece", current_stock=Decimal(7), min_stock=Decimal(7)
        )
        InventoryItem.objects.create(
            name="کمتر", unit="piece", current_stock=Decimal(0), min_stock=Decimal(12)
        )
        InventoryItem.objects.create(
            name="بیشتر", unit="piece", current_stock=Decimal(13), min_stock=Decimal(12)
        )
        self.w.milk.current_stock = Decimal(100)  # دقیقاً برابر min_stock
        self.w.milk.save()
        names = [i["name"] for i in self._summary()["low_stock_items"]]
        self.assertEqual(names, ["کمتر", "شیر", "برابر"])  # کمترین نسبت اول
        self.assertNotIn("بیشتر", names)

    def test_no_threshold_means_always_low(self):
        """min_stock=۰ یعنی آستانه‌ای تعریف نشده؛ رفتار فعلی پروژه: همیشه کم."""
        InventoryItem.objects.create(
            name="بی‌آستانه", unit="piece", current_stock=Decimal(0), min_stock=Decimal(0)
        )
        self.assertEqual(
            [i["name"] for i in self._summary()["low_stock_items"]], ["بی‌آستانه"]
        )