"""تست‌های مهمان‌نوازی.

قانون‌هایی که اینجا محافظت می‌شوند:
- مهمان سفارش را می‌بندد ولی هیچ Payment/صندوق/بانک/طلبی نمی‌سازد.
- درآمد و هزینه‌ی مهمان‌نوازی هم‌اندازه‌اند؛ پس سود و زیان فقط به اندازه‌ی
  بهای تمام‌شده کاهش می‌یابد و دفتر `health()` سبز می‌ماند.
- لغو، سند را معکوس و موجودی را دقیقاً یک بار برمی‌گرداند (بدون دوباره‌شماری).
- گروه‌بندی بر اساس نام نرمال‌شده انجام می‌شود و مبلغ از خودِ سفارش می‌آید.
"""

import json

from django.test import Client, TestCase

from core import ledger
from core.models import CashAccount, JournalKind, LedgerAccount
from core.security import TICKET_HEADER
from core.testing import make_financial_world
from guests import services
from guests.models import Guest
from inventory.models import InventoryItem
from orders import services as order_services
from orders.models import Payment


def post(c, url, body=None):
    return c.post(url, json.dumps(body or {}), content_type="application/json")


class GuestBase(TestCase):
    def setUp(self):
        self.w = make_financial_world()
        self.api = Client(
            headers={
                "Authorization": f"Token {self.w.token}",
                TICKET_HEADER: self.w.security_ticket,
            }
        )

    def cake_order(self, table, qty=2):
        """کیک بدون Recipe ⇒ بهای تمام‌شده صفر و بررسی برگشت ساده است."""
        return order_services.create_order(
            table_id=table.id, source="admin",
            items=[{"product_id": self.w.cake.id, "quantity": qty}],
        )

    def latte_order(self, table, qty=1):
        return order_services.create_order(
            table_id=table.id, source="admin",
            items=[{"product_id": self.w.latte.id, "quantity": qty}],
        )

    def mark(self, order, name="مهمان اول"):
        return order_services.pay_order(
            order.id, "guest", self.w.admin, guest_name=name
        )

    def stock(self, item):
        item.refresh_from_db()
        return item.current_stock


class GuestSaleTests(GuestBase):
    def test_guest_books_expense_and_revenue_without_payment(self):
        o = self.cake_order(self.w.t2, qty=2)  # ۲۲۰٬۰۰۰
        self.mark(o, "علی رضا")
        o.refresh_from_db()

        self.assertEqual(
            (o.status, o.payment_status, o.payment_method),
            ("paid", "guest", "guest"),
        )
        # هیچ پرداختی ثبت نمی‌شود و پولی هم وارد نمی‌شود.
        self.assertEqual(Payment.objects.filter(order=o).count(), 0)
        self.assertEqual(
            (ledger.balance(CashAccount.CASH), ledger.balance(CashAccount.BANK),
             ledger.balance(LedgerAccount.RECEIVABLE)),
            (0, 0, 0),
        )
        g = Guest.objects.get(order=o)
        self.assertEqual((g.name, g.amount, g.status),
                         ("علی رضا", 220000, Guest.Status.ACTIVE))
        # درآمد و هزینه‌ی مهمان‌نوازی هم‌اندازه ⇒ سود خالص صفر (بدون Recipe).
        pl = ledger.profit_and_loss()
        self.assertEqual((pl["revenue"], pl["expenses"], pl["net_profit"]),
                         (220000, 220000, 0))
        self.assertTrue(ledger.health()["ok"], ledger.health()["problems"])

    def test_guest_requires_name(self):
        o = self.cake_order(self.w.t2)
        with self.assertRaisesMessage(Exception, "نام مهمان"):
            self.mark(o, "")
        o.refresh_from_db()
        self.assertEqual(o.payment_status, "unpaid")
        self.assertEqual(Guest.objects.count(), 0)

    def test_guest_name_is_normalized(self):
        o = self.cake_order(self.w.t2)
        self.mark(o, "  علی   رضا  ")
        self.assertEqual(Guest.objects.get().name, "علی رضا")

    def test_name_too_long_is_rejected(self):
        o = self.cake_order(self.w.t2)
        with self.assertRaisesMessage(Exception, "حداکثر"):
            self.mark(o, "ا" * 81)
        self.assertEqual(Guest.objects.count(), 0)

    def test_cannot_guest_a_paid_order(self):
        o = self.cake_order(self.w.t2)
        order_services.pay_order(o.id, "cash", self.w.admin)
        with self.assertRaisesMessage(Exception, "پرداخت‌نشده"):
            self.mark(o)

    def test_duplicate_guest_is_idempotent(self):
        o = self.cake_order(self.w.t2)
        self.mark(o, "سمی")
        self.mark(o, "سمی")
        self.assertEqual(Guest.objects.count(), 1)
        self.assertEqual(ledger.profit_and_loss()["revenue"], 220000)

    def test_guest_does_not_double_consume_inventory(self):
        o = self.latte_order(self.w.t2, qty=1)
        self.assertEqual(self.stock(self.w.milk), 800)  # ۱۰۰۰ − ۲۰۰
        self.assertEqual(self.stock(self.w.beans), 482)  # ۵۰۰ − ۱۸
        self.mark(o)
        self.assertEqual(self.stock(self.w.milk), 800)
        self.assertEqual(self.stock(self.w.beans), 482)

    def test_guest_is_not_a_monetary_payment_method(self):
        o = self.cake_order(self.w.t2)
        self.mark(o)
        methods = ledger.payment_methods()
        self.assertEqual(methods, [])  # فقط ورودی‌های فروش؛ مهمان ورودی SALE ندارد
        self.assertEqual(ledger.profit_and_loss()["revenue"], 220000)


class GuestTableTests(GuestBase):
    def test_pay_table_guest_closes_all_orders_and_session(self):
        a = self.cake_order(self.w.t2, qty=1)  # ۱۱۰٬۰۰۰
        b = self.cake_order(self.w.t2, qty=2)  # ۲۲۰٬۰۰۰
        order_services.pay_table(self.w.t2.id, "guest", self.w.admin,
                                 guest_name="خانواده احمدی")
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((a.status, b.status), ("paid", "paid"))
        self.assertEqual((a.payment_status, b.payment_status), ("guest", "guest"))
        self.assertEqual(Guest.objects.count(), 2)
        self.assertEqual(services.guest_total(), 330000)
        # نشست میز بسته و میز خالی شده است.
        a.session.refresh_from_db()
        self.assertIsNotNone(a.session.exited_at)
        self.w.t2.refresh_from_db()
        self.assertEqual(self.w.t2.status, "empty")
        self.assertTrue(ledger.health()["ok"], ledger.health()["problems"])

    def test_guest_mix_keeps_same_name_group(self):
        self.cake_order(self.w.t2, qty=1)
        self.cake_order(self.w.t5, qty=2)
        order_services.pay_table(self.w.t2.id, "guest", self.w.admin, guest_name="سارا")
        order_services.pay_table(self.w.t5.id, "guest", self.w.admin, guest_name="سارا")
        groups = list(services.guest_groups())
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["total_amount"], 330000)
        self.assertEqual(groups[0]["order_count"], 2)


class GuestCancelTests(GuestBase):
    def test_cancel_reverses_ledger_and_restores_inventory(self):
        o = self.latte_order(self.w.t2, qty=1)  # ۹۵٬۰۰۰
        self.mark(o)
        g = Guest.objects.get(order=o)
        self.assertEqual(ledger.profit_and_loss()["expenses"], 95000)

        services.cancel_guest(guest_id=g.id, user=self.w.admin)
        o.refresh_from_db()
        g.refresh_from_db()
        self.assertEqual((o.status, o.payment_status), ("cancelled", "refunded"))
        self.assertEqual(g.status, Guest.Status.CANCELLED)
        # موجودی دقیقاً یک بار برگشته است.
        self.assertEqual(self.stock(self.w.milk), 1000)
        self.assertEqual(self.stock(self.w.beans), 500)
        # سند مهمان‌نوازی و بهای تمام‌شده هر دو خنثا شده‌اند.
        pl = ledger.profit_and_loss()
        self.assertEqual(
            (pl["revenue"], pl["expenses"], pl["cogs"], pl["net_profit"]),
            (0, 0, 0, 0),
        )
        self.assertTrue(ledger.health()["ok"], ledger.health()["problems"])

    def test_cancel_is_idempotent(self):
        o = self.cake_order(self.w.t2)
        self.mark(o)
        g = Guest.objects.get()
        services.cancel_guest(guest_id=g.id, user=self.w.admin)
        services.cancel_guest(guest_id=g.id, user=self.w.admin)
        self.assertEqual(ledger.profit_and_loss()["revenue"], 0)
        self.assertTrue(ledger.health()["ok"], ledger.health()["problems"])

    def test_cancel_only_touches_its_own_order(self):
        a = self.cake_order(self.w.t2, qty=1)
        b = self.cake_order(self.w.t5, qty=2)
        self.mark(a, "اولی")
        self.mark(b, "دومی")
        services.cancel_guest(guest_id=Guest.objects.get(order=a).id,
                              user=self.w.admin)
        self.assertEqual(services.guest_total(), 220000)
        self.assertEqual(Guest.objects.get(order=b).status, Guest.Status.ACTIVE)
        self.assertTrue(ledger.health()["ok"], ledger.health()["problems"])


class GuestApiTests(GuestBase):
    def test_endpoints_require_admin(self):
        self.assertNotEqual(Client().get("/api/v1/guests/").status_code, 200)

    def test_list_groups_and_summary(self):
        self.mark(self.cake_order(self.w.t2, qty=1), "علی")
        self.mark(self.cake_order(self.w.t5, qty=2), "علی")
        self.mark(self.cake_order(self.w.t8, qty=1), "مریم")

        groups = self.api.get("/api/v1/guests/").json()
        by_name = {g["name"]: g for g in groups}
        self.assertEqual(by_name["علی"]["total_amount"], 330000)
        self.assertEqual(by_name["علی"]["order_count"], 2)
        self.assertEqual(by_name["مریم"]["total_amount"], 110000)

        summary = self.api.get("/api/v1/guests/summary/").json()
        self.assertEqual(summary["total_amount"], 440000)
        self.assertEqual(summary["guest_count"], 3)

    def test_search_groups(self):
        self.mark(self.cake_order(self.w.t2), "مریم")
        self.mark(self.cake_order(self.w.t5), "علی")
        r = self.api.get("/api/v1/guests/?q=مریم").json()
        self.assertEqual([g["name"] for g in r], ["مریم"])

    def test_orders_endpoint_lists_a_name(self):
        self.mark(self.cake_order(self.w.t2, qty=1), "علی")
        self.mark(self.cake_order(self.w.t5, qty=2), "علی")
        rows = self.api.get("/api/v1/guests/orders/?name=علی").json()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["amount"] for r in rows}, {110000, 220000})
        self.assertEqual(self.api.get("/api/v1/guests/orders/").status_code, 400)

    def test_rename_moves_group_without_touching_ledger(self):
        self.mark(self.cake_order(self.w.t2, qty=1), "علی")
        g = Guest.objects.get()
        r = self.api.patch(f"/api/v1/guests/{g.id}/", json.dumps({"name": "حسن"}),
                           content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "حسن")
        self.assertEqual(services.guest_total(), 110000)
        self.assertEqual(ledger.profit_and_loss()["revenue"], 110000)

    def test_patch_requires_name(self):
        self.mark(self.cake_order(self.w.t2))
        g = Guest.objects.get()
        r = self.api.patch(f"/api/v1/guests/{g.id}/", json.dumps({}),
                           content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_cancel_endpoint(self):
        self.mark(self.cake_order(self.w.t2))
        g = Guest.objects.get()
        r = post(self.api, f"/api/v1/guests/{g.id}/cancel/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "cancelled")
        self.assertEqual(self.api.get("/api/v1/guests/").json(), [])

    def test_unknown_guest_is_404(self):
        self.assertEqual(self.api.get("/api/v1/guests/9999/").status_code, 404)
        self.assertEqual(post(self.api, "/api/v1/guests/9999/cancel/").status_code, 404)


class GuestReportTests(GuestBase):
    def test_report_exposes_guest_hospitality(self):
        self.mark(self.cake_order(self.w.t2, qty=2))  # ۲۲۰٬۰۰۰
        r = self.api.get("/api/v1/reports/?period=month").json()
        self.assertEqual(r["guest_hospitality"], 220000)
        self.assertEqual(r["total_sales"], 220000)  # مهمان در فروش می‌ماند
        self.assertEqual(r["total_expenses"], 220000)
        self.assertEqual(r["net_profit"], 0)
        self.assertEqual(r["cash_received"], 0)

    def test_guest_costs_only_the_cogs(self):
        self.w.beans.unit_cost = 1000
        self.w.beans.save(update_fields=["unit_cost"])
        self.w.milk.unit_cost = 100
        self.w.milk.save(update_fields=["unit_cost"])
        # موجودی اولیه‌ی این دو کالا در دفتر ثبت نشده بود؛ بدون آن بازرسی
        # «موجودی دفتر = ارزش کالا» به‌خاطر خودِ محیط تست قرمز می‌شود.
        ledger.post_opening_stock(item=self.w.beans, amount=500 * 1000)
        ledger.post_opening_stock(item=self.w.milk, amount=1000 * 100)
        self.mark(self.latte_order(self.w.t2, qty=1))  # COGS = ۲۰۰*۱۰۰ + ۱۸*۱۰۰۰
        pl = ledger.profit_and_loss()
        self.assertEqual((pl["revenue"], pl["expenses"], pl["cogs"]),
                         (95000, 95000, 38000))
        self.assertEqual(pl["net_profit"], -38000)  # زیان = بهای تمام‌شده
        self.assertTrue(ledger.health()["ok"], ledger.health()["problems"])

    def test_transactions_endpoint_marks_guest_kinds(self):
        o = self.cake_order(self.w.t2)
        self.mark(o)
        rows = self.api.get("/api/v1/accounting/transactions/").json()
        by_kind = {r["kind"]: r for r in rows}
        self.assertEqual(by_kind[JournalKind.GUEST]["type"], "expense")
        self.assertEqual(by_kind[JournalKind.GUEST]["amount"], 220000)
        self.assertEqual(
            by_kind[JournalKind.GUEST]["title"], f"مهمان‌نوازی سفارش {o.number}"
        )
