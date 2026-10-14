"""تست‌های قابلیت آفر: سرویس قیمت‌گذاری مرکزی و endpointهای مدیریتی."""

import json
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from catalog import pricing
from catalog.models import Offer
from core.testing import make_world, scan
from orders import services as order_services
from orders.models import Order


def api(client, token):
    return Client(headers={"Authorization": f"Token {token}"})


def post(c, url, body):
    return c.post(url, json.dumps(body), content_type="application/json")


class PricingServiceTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.latte = self.w.latte  # ۹۵٬۰۰۰

    def _offer(self, *, percent, product=None, category=None, scope="product", **kw):
        return Offer.objects.create(
            scope=scope, product=product, category=category, percent=percent, **kw
        )

    def test_no_offer_returns_base(self):
        q = pricing.quote_product(self.latte)
        self.assertEqual(q["base"], 95000)
        self.assertEqual(q["final"], 95000)
        self.assertEqual(q["total_percent"], 0)
        self.assertEqual(q["percentages"], [])

    def test_single_percent(self):
        self._offer(percent=20, product=self.latte)
        q = pricing.quote_product(self.latte)
        self.assertEqual(q["final"], 76000)
        self.assertEqual(q["total_percent"], 20)

    def test_sequential_offers_apply_on_discounted_price(self):
        self._offer(percent=20, product=self.latte)
        self._offer(percent=10, product=self.latte)
        # ۹۵٬۰۰۰ × ۰٫۸ = ۷۶٬۰۰۰ و × ۰٫۹ = ۶۸٬۴۰۰ (نه ۷۰٪ یک‌جا)
        q = pricing.quote_product(self.latte)
        self.assertEqual(q["final"], 68400)
        self.assertEqual(q["base"], 95000)

    def test_category_and_product_offer_stack_once_each(self):
        self._offer(percent=20, product=self.latte)
        self._offer(percent=10, category=self.w.cat, scope="category")
        q = pricing.quote_product(self.latte)
        # هر دو آفر یک‌بار: ۷۶٬۰۰۰ سپس ۶۸٬۴۰۰ — تخفیف دوبار حساب نمی‌شود
        self.assertEqual(q["final"], 68400)
        self.assertEqual(len(q["percentages"]), 2)

    def test_inactive_offer_is_ignored(self):
        self._offer(percent=20, product=self.latte, is_active=False)
        self.assertEqual(pricing.quote_product(self.latte)["final"], 95000)

    def test_future_offer_is_ignored_until_start(self):
        self._offer(percent=20, product=self.latte,
                    starts_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(pricing.quote_product(self.latte)["final"], 95000)

    def test_expired_offer_is_ignored(self):
        self._offer(percent=20, product=self.latte,
                    ends_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(pricing.quote_product(self.latte)["final"], 95000)

    def test_open_ended_offer_stays_valid(self):
        self._offer(percent=20, product=self.latte)  # بدون starts/ends
        self.assertEqual(pricing.quote_product(self.latte)["final"], 76000)

    def test_rounding_is_half_up(self):
        # ۹۹۹ × ۱٪ تخفیف = ۹۸۹٫۰۱ ⇒ ۹۸۹
        self.assertEqual(pricing.apply_percent(999, 1), 989)
        # ۵۰ × ۱٪ = ۴۹٫۵ ⇒ ۵۰ با نیم‌بها به بالا
        self.assertEqual(pricing.apply_percent(50, 1), 50)

    def test_move_product_between_categories_recomputes(self):
        other = type(self.w.cat).objects.create(name="دسر")
        self._offer(percent=10, category=self.w.cat, scope="category")
        self.assertEqual(pricing.quote_product(self.latte)["final"], 85500)
        self.latte.category = other
        self.latte.save(update_fields=["category"])
        # آفر دسته‌ی قبلی دیگر اعمال نمی‌شود (بدون از دست رفتن قیمت پایه)
        self.assertEqual(pricing.quote_product(self.latte)["final"], 95000)
        self.assertEqual(self.latte.price, 95000)


class OfferEndpointTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.c = api(Client(), self.w.token)

    def test_preview_does_not_create_or_change_offers(self):
        r = post(self.c, "/api/v1/offers/preview/", {
            "scope": "product", "product_id": self.w.latte.id, "percent": 20})
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertEqual(data["products"][0]["base"], 95000)
        self.assertEqual(data["products"][0]["before"], 95000)
        self.assertEqual(data["products"][0]["final"], 76000)
        self.assertEqual(Offer.objects.count(), 0)

    def test_create_requires_unchanged_fingerprint(self):
        preview = post(self.c, "/api/v1/offers/preview/", {
            "scope": "product", "product_id": self.w.latte.id, "percent": 20}).json()
        r = post(self.c, "/api/v1/offers/create/", {
            "scope": "product", "product_id": self.w.latte.id, "percent": 20,
            "fingerprint": preview["fingerprint"]})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Offer.objects.count(), 1)

        # همان پیش‌نمایش دیگر معتبر نیست چون آفر جدید قیمت را عوض کرده است.
        r2 = post(self.c, "/api/v1/offers/create/", {
            "scope": "product", "product_id": self.w.latte.id, "percent": 10,
            "fingerprint": preview["fingerprint"]})
        self.assertEqual(r2.status_code, 409)
        self.assertEqual(Offer.objects.count(), 1)

    def test_stale_preview_rejected_when_price_changes(self):
        preview = post(self.c, "/api/v1/offers/preview/", {
            "scope": "product", "product_id": self.w.latte.id, "percent": 20}).json()
        self.w.latte.price = 100000
        self.w.latte.save(update_fields=["price"])
        r = post(self.c, "/api/v1/offers/create/", {
            "scope": "product", "product_id": self.w.latte.id, "percent": 20,
            "fingerprint": preview["fingerprint"]})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(Offer.objects.count(), 0)

    def test_category_preview_lists_active_products(self):
        r = post(self.c, "/api/v1/offers/preview/", {
            "scope": "category", "category_id": self.w.cat.id, "percent": 50}).json()
        ids = {p["product_id"] for p in r["products"]}
        self.assertIn(self.w.latte.id, ids)
        self.assertIn(self.w.cake.id, ids)
        self.assertNotIn(self.w.off.id, ids)  # غیرفعال‌ها مشمول نمی‌شوند

    def test_percent_bounds_enforced(self):
        for pct in (0, 100, 101, -5):
            r = post(self.c, "/api/v1/offers/preview/", {
                "scope": "product", "product_id": self.w.latte.id, "percent": pct})
            self.assertEqual(r.status_code, 400, pct)

    def test_end_before_start_rejected(self):
        r = post(self.c, "/api/v1/offers/preview/", {
            "scope": "product", "product_id": self.w.latte.id, "percent": 10,
            "starts_at": "2026-01-02T00:00:00+00:00",
            "ends_at": "2026-01-01T00:00:00+00:00"})
        self.assertEqual(r.status_code, 400)

    def test_non_staff_cannot_manage_offers(self):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user("plain", password="x")
        from rest_framework.authtoken.models import Token
        t = Token.objects.create(user=u)
        c = api(Client(), t.key)
        r = post(c, "/api/v1/offers/preview/", {
            "scope": "product", "product_id": self.w.latte.id, "percent": 20})
        self.assertIn(r.status_code, (401, 403))

    def test_deactivate_removes_discount(self):
        offer = Offer.objects.create(scope="product", product=self.w.latte, percent=20)
        self.assertEqual(pricing.quote_product(self.w.latte)["final"], 76000)
        r = post(self.c, f"/api/v1/offers/{offer.id}/deactivate/", {})
        self.assertEqual(r.status_code, 200)
        offer.refresh_from_db()
        self.assertFalse(offer.is_active)
        self.assertEqual(pricing.quote_product(self.w.latte)["final"], 95000)


class OfferOrderSnapshotTests(TestCase):
    """آفر فقط روی سفارش‌های جدید اثر دارد؛ سفارش‌های قبلی دست‌نخورده می‌مانند."""

    def setUp(self):
        self.w = make_world()

    def test_order_snapshots_effective_price_and_base(self):
        Offer.objects.create(scope="product", product=self.w.latte, percent=20)
        order = order_services.create_order(
            table_id=self.w.t5.id, source="admin",
            items=[{"product_id": self.w.latte.id, "quantity": 2}])
        item = order.items.get()
        self.assertEqual(item.original_price, 95000)
        self.assertEqual(item.unit_price, 76000)
        self.assertEqual(item.discount_amount, 19000)
        self.assertEqual(order.total, 152000)
        # قیمت پایه‌ی محصول تغییر نکرده
        self.w.latte.refresh_from_db()
        self.assertEqual(self.w.latte.price, 95000)

    def test_previous_order_is_unchanged_after_new_offer(self):
        first = order_services.create_order(
            table_id=self.w.t5.id, source="admin",
            items=[{"product_id": self.w.cake.id, "quantity": 1}])
        self.assertEqual(first.total, 110000)
        Offer.objects.create(scope="product", product=self.w.cake, percent=50)
        first.refresh_from_db()
        self.assertEqual(first.total, 110000)
        self.assertEqual(first.items.get().unit_price, 110000)
        self.assertEqual(first.items.get().discount_amount, 0)
        # سفارش جدید با قیمت تخفیف‌خورده
        second = order_services.create_order(
            table_id=self.w.t5.id, source="admin",
            items=[{"product_id": self.w.cake.id, "quantity": 1}])
        self.assertEqual(second.total, 55000)
        self.assertEqual(Order.objects.get(pk=first.pk).total, 110000)


class CustomerMenuOfferTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.c = Client()

    def _products(self):
        scan(self.c, self.w.t2)
        menu = self.c.get("/api/customer/menu/").json()
        return {p["name"]: p for cat in menu["categories"] for p in cat["products"]}

    def test_menu_shows_original_and_effective_price(self):
        Offer.objects.create(scope="product", product=self.w.latte, percent=20)
        latte = self._products()["لاته"]
        self.assertEqual(latte["original_price"], 95000)
        self.assertEqual(latte["price"], 76000)
        self.assertEqual(latte["discount_percent"], 20)
        self.assertTrue(latte["has_offer"])

    def test_menu_without_offer_is_normal(self):
        latte = self._products()["لاته"]
        self.assertEqual(latte["price"], 95000)
        self.assertFalse(latte["has_offer"])

    def test_expired_offer_not_shown_to_customer(self):
        Offer.objects.create(scope="product", product=self.w.latte, percent=20,
                             ends_at=timezone.now() - timedelta(minutes=1))
        latte = self._products()["لاته"]
        self.assertEqual(latte["price"], 95000)
        self.assertFalse(latte["has_offer"])

    def test_customer_order_charges_effective_price(self):
        Offer.objects.create(scope="product", product=self.w.latte, percent=20)
        scan(self.c, self.w.t2)
        r = post(self.c, "/api/customer/orders/",
                 {"items": [{"product_id": self.w.latte.id, "quantity": 1,
                             "price": 1, "unit_price": 1}]})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["total"], 76000)  # قیمت کلاینت نادیده گرفته می‌شود
        item = r.json()["items"][0]
        self.assertEqual((item["original_price"], item["unit_price"], item["discount_amount"]),
                         (95000, 76000, 19000))
