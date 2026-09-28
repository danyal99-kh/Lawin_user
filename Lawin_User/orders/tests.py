import json
from decimal import Decimal

from django.test import Client, TestCase

from core.errors import OutOfStock
from core.models import WelcomeMessage
from core.testing import make_world, scan
from inventory.models import InventoryItem, InventoryTransaction
from orders import services
from orders.models import Order, Payment
from tables.models import Table, TableSession


def post(c, url, body):
    return c.post(url, json.dumps(body), content_type="application/json")


class CustomerFlowTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.c = Client()

    def test_scenario1_qr_welcome_menu_order(self):
        r = scan(self.c, self.w.t2)
        self.assertRedirects(r, "/welcome/", fetch_redirect_response=False)
        self.assertContains(self.c.get("/welcome/"), WelcomeMessage.load().title)
        self.assertEqual(self.c.get("/menu/").status_code, 200)
        menu = self.c.get("/api/customer/menu/").json()
        names = {p["name"]: p for cat in menu["categories"] for p in cat["products"]}
        self.assertTrue(names["لاته"]["available"])
        self.assertFalse(names["موکا"]["available"])  # غیرفعال
        r = post(self.c, "/api/customer/orders/", {"items": [{"product_id": self.w.latte.id, "quantity": 2, "note": "بدون شکر"}]})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["total"], 190000)
        self.w.t2.refresh_from_db()
        self.assertEqual(self.w.t2.status, Table.Status.ACTIVE)
        self.assertEqual(TableSession.objects.filter(table=self.w.t2, exited_at=None).count(), 1)

    def test_welcome_disabled_goes_to_menu(self):
        WelcomeMessage.objects.update_or_create(pk=1, defaults={"enabled": False})
        self.assertRedirects(scan(self.c, self.w.t2), "/menu/", fetch_redirect_response=False)

    def test_invalid_token_and_no_session(self):
        self.assertEqual(self.c.get("/menu/table/2/").status_code, 404)  # شماره‌ی میز QR معتبر نیست
        self.assertEqual(self.c.get("/api/customer/menu/").status_code, 403)
        self.assertEqual(Client().get("/menu/").status_code, 403)

    def test_scenario4_insufficient_stock_is_atomic(self):
        scan(self.c, self.w.t2)
        r = post(self.c, "/api/customer/orders/", {"items": [{"product_id": self.w.latte.id, "quantity": 6}]})  # ۱۲۰۰ml > ۱۰۰۰
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "insufficient_stock")
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(TableSession.objects.count(), 0)
        self.w.milk.refresh_from_db()
        self.assertEqual(self.w.milk.current_stock, Decimal(1000))
        self.w.t2.refresh_from_db()
        self.assertEqual(self.w.t2.status, Table.Status.EMPTY)

    def test_inactive_product_rejected(self):
        scan(self.c, self.w.t2)
        r = post(self.c, "/api/customer/orders/", {"items": [{"product_id": self.w.off.id, "quantity": 1}]})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "inactive_product")

    def test_scenario5_price_tampering_ignored(self):
        scan(self.c, self.w.t2)
        r = post(self.c, "/api/customer/orders/", {"total": 1, "items": [
            {"product_id": self.w.cake.id, "quantity": 1, "price": 1, "unit_price": 1, "total": 1}]})
        self.assertEqual(r.status_code, 201)
        o = Order.objects.get()
        self.assertEqual((o.total, o.items.get().unit_price), (110000, 110000))

    def test_scenario7_recipe_deducts_inventory(self):
        scan(self.c, self.w.t2)
        post(self.c, "/api/customer/orders/", {"items": [{"product_id": self.w.latte.id, "quantity": 2}]})
        self.w.milk.refresh_from_db(); self.w.beans.refresh_from_db()
        self.assertEqual((self.w.milk.current_stock, self.w.beans.current_stock), (Decimal(600), Decimal(464)))
        self.assertEqual(InventoryTransaction.objects.filter(kind="order_consume").count(), 2)

    def test_cancel_restores_inventory_and_closes_session(self):
        scan(self.c, self.w.t2)
        o = Order.objects.get(pk=post(self.c, "/api/customer/orders/", {"items": [{"product_id": self.w.latte.id, "quantity": 2}]}).json()["id"])
        services.change_status(o.id, "cancelled")
        self.w.milk.refresh_from_db(); self.w.t2.refresh_from_db()
        self.assertEqual(self.w.milk.current_stock, Decimal(1000))
        self.assertEqual(self.w.t2.status, Table.Status.EMPTY)

    def test_multiple_orders_same_session(self):
        scan(self.c, self.w.t2)
        for pid in (self.w.latte.id, self.w.cake.id):
            post(self.c, "/api/customer/orders/", {"items": [{"product_id": pid, "quantity": 1}]})
        a, b = Order.objects.order_by("number")
        self.assertEqual(a.session_id, b.session_id)
        self.assertEqual(b.number, a.number + 1)

    def test_customer_cannot_see_other_orders(self):
        scan(self.c, self.w.t2)
        oid = post(self.c, "/api/customer/orders/", {"items": [{"product_id": self.w.cake.id, "quantity": 1}]}).json()["id"]
        other = Client(); scan(other, self.w.t5)
        self.assertEqual(other.get(f"/api/customer/orders/{oid}/").status_code, 404)
        self.assertEqual(other.get("/api/customer/orders/").json()["orders"], [])
        scan(other, self.w.t2)  # حتی با اسکن همان میز، مرورگر دیگری سفارش شما را نمی‌بیند
        self.assertEqual(other.get(f"/api/customer/orders/{oid}/").status_code, 404)

    def test_bad_payloads(self):
        scan(self.c, self.w.t2)
        for body in ({"items": []}, {"items": [{"product_id": self.w.cake.id, "quantity": 0}]},
                     {"items": [{"product_id": self.w.cake.id, "quantity": 999}]}, {"items": "x"},
                     {"items": [{"product_id": 999999, "quantity": 1}]}):
            self.assertIn(post(self.c, "/api/customer/orders/", body).status_code, (400, 404), body)

    def test_scenario8_admin_payment_closes_session(self):
        scan(self.c, self.w.t2)
        oid = post(self.c, "/api/customer/orders/", {"items": [{"product_id": self.w.cake.id, "quantity": 2}]}).json()["id"]
        api = Client(headers={"Authorization": f"Token {self.w.token}"})
        self.assertEqual(api.post(f"/api/v1/orders/{oid}/pay/", json.dumps({"method": "cash"}), content_type="application/json").status_code, 200)
        o = Order.objects.get(pk=oid)
        self.assertEqual((o.status, o.payment_status, o.payment_method), ("paid", "paid", "cash"))
        self.assertEqual(Payment.objects.get(order=o).amount, 220000)
        s = TableSession.objects.get(pk=o.session_id)
        self.assertIsNotNone(s.exited_at)
        self.w.t2.refresh_from_db()
        self.assertEqual(self.w.t2.status, Table.Status.EMPTY)

    def test_admin_api_requires_token(self):
        self.assertEqual(Client().get("/api/v1/tables/").status_code, 401)
        api = Client(headers={"Authorization": f"Token {self.w.token}"})
        r = api.get("/api/v1/tables/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()), 3)
