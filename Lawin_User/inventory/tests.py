import json
from unittest.mock import patch

from django.db.models.query import QuerySet
from django.test import Client, TestCase

from core.testing import make_world
from inventory.models import InventoryItem, InventoryTransaction

URL = "/api/v1/inventory/items/"
PURCHASES_URL = "/api/v1/inventory/purchases/"
WASTES_URL = "/api/v1/inventory/wastes/"


class InventoryApiTests(TestCase):
    def setUp(self):
        self.w = make_world()  # شامل «شیر» و «قهوه» که در دستور مصرف لاته‌اند
        self.api = Client(headers={"Authorization": f"Token {self.w.token}"})

    def _post(self, body):
        return self.api.post(URL, json.dumps(body), content_type="application/json")

    def _patch(self, pk, body):
        return self.api.patch(
            f"{URL}{pk}/", json.dumps(body), content_type="application/json"
        )

    def test_requires_token(self):
        self.assertEqual(Client().get(URL).status_code, 401)

    def test_create_with_initial_stock_writes_ledger(self):
        r = self._post(
            {
                "name": "شکر",
                "unit": "g",
                "min_stock": 500,
                "unit_cost": 40,
                "initial_stock": 2000,
                "description": "",
            }
        )
        self.assertEqual(r.status_code, 201, r.content)
        j = r.json()
        self.assertEqual(
            (j["current_stock"], j["min_stock"], j["description"]),
            (2000.0, 500.0, None),
        )
        t = InventoryTransaction.objects.get(item_id=j["id"])
        self.assertEqual((t.kind, float(t.quantity)), ("adjust", 2000.0))

    def test_duplicate_name_normalized(self):
        r = self._post({"name": "شير", "unit": "ml"})  # «ي» عربی = «شیر»
        self.assertEqual(r.status_code, 400)
        self.assertEqual(
            r.json()["error"]["message"], "کالایی با این نام قبلاً ثبت شده است."
        )

    def test_invalid_values(self):
        for body in (
            {"name": "", "unit": "g"},
            {"name": "الف", "unit": "kg"},
            {"name": "ب", "unit": "g", "min_stock": -1},
            {"name": "پ", "unit": "g", "unit_cost": "abc"},
        ):
            self.assertEqual(self._post(body).status_code, 400, body)

    def test_patch_ignores_unit_and_stock(self):
        r = self._patch(
            self.w.milk.id,
            {"name": "شیر پرچرب", "unit": "g", "current_stock": 5, "min_stock": 300},
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.w.milk.refresh_from_db()
        self.assertEqual(
            (
                self.w.milk.name,
                self.w.milk.unit,
                float(self.w.milk.current_stock),
                float(self.w.milk.min_stock),
            ),
            ("شیر پرچرب", "ml", 1000.0, 300.0),
        )

    def test_delete_rules(self):
        self.assertEqual(
            self.api.delete(f"{URL}{self.w.milk.id}/").status_code, 409
        )  # موجودی دارد
        self.w.milk.current_stock = 0
        self.w.milk.save()
        self.assertEqual(
            self.api.delete(f"{URL}{self.w.milk.id}/").status_code, 409
        )  # در دستور مصرف
        free = self._post({"name": "کاغذ", "unit": "piece"}).json()["id"]
        self.assertEqual(self.api.delete(f"{URL}{free}/").status_code, 204)
        self.assertFalse(InventoryItem.objects.filter(pk=free).exists())
        self.assertEqual(self.api.delete(f"{URL}{free}/").status_code, 404)


class InventoryLedgerApiTests(TestCase):
    """خرید و ضایعات: اثر روی موجودی، دفتر حرکت، و قوانین اعتبارسنجی."""

    def setUp(self):
        self.w = make_world()
        self.api = Client(headers={"Authorization": f"Token {self.w.token}"})

    def _post(self, url, body):
        return self.api.post(url, json.dumps(body), content_type="application/json")

    def _buy(self, **over):
        body = {"item_id": self.w.milk.id, "quantity": 500, "unit_cost": 12000}
        body.update(over)
        return self._post(PURCHASES_URL, body)

    def _waste(self, **over):
        body = {"item_id": self.w.milk.id, "quantity": 200, "reason": "expired"}
        body.update(over)
        return self._post(WASTES_URL, body)

    # ---------- احراز هویت ----------
    def test_endpoints_require_token(self):
        self.assertEqual(Client().get(PURCHASES_URL).status_code, 401)
        self.assertEqual(Client().get(WASTES_URL).status_code, 401)

    # ---------- خرید ----------
    def test_purchase_increases_stock_and_writes_ledger(self):
        before = self.w.milk.current_stock
        r = self._buy(quantity=500, unit_cost=12000)
        self.assertEqual(r.status_code, 201, r.content)
        j = r.json()

        self.w.milk.refresh_from_db()
        self.assertEqual(float(self.w.milk.current_stock), float(before) + 500)
        self.assertEqual(float(self.w.milk.unit_cost), 12000.0)  # آخرین قیمت خرید

        # پاسخ دقیقاً مطابق Purchase.fromJson در Flutter
        self.assertEqual(
            (j["id"], j["item_id"], j["item_name"], j["unit"]),
            (j["id"], self.w.milk.id, "شیر", "ml"),
        )
        self.assertEqual((j["quantity"], j["unit_cost"], j["total_cost"]),
                         (500.0, 12000.0, 6000000.0))
        self.assertIn("purchased_at", j)

        t = InventoryTransaction.objects.get(pk=j["id"])
        self.assertEqual(t.kind, InventoryTransaction.Kind.PURCHASE)
        self.assertEqual(float(t.quantity), 500.0)
        self.assertEqual(float(t.unit_cost), 12000.0)
        self.assertEqual(t.item_name_snapshot, "شیر")  # snapshot نام کالا

    def test_purchase_list_returns_snapshot_after_rename(self):
        pid = self._buy(quantity=100).json()["id"]
        renamed = self.api.patch(
            f"{URL}{self.w.milk.id}/",
            json.dumps({"name": "شیر پرچرب"}),
            content_type="application/json",
        )
        self.assertEqual(renamed.status_code, 200, renamed.content)

        row = next(r for r in self.api.get(PURCHASES_URL).json() if r["id"] == pid)
        self.assertEqual(row["item_name"], "شیر")  # نامِ لحظه‌ی خرید حفظ شد
        self.w.milk.refresh_from_db()
        self.assertEqual(self.w.milk.name, "شیر پرچرب")  # ولی کالا rename شده

    def test_purchase_validation(self):
        cases = [
            ({"quantity": 0}, "مقدار خرید باید بیشتر از صفر باشد."),
            ({"quantity": -5}, "مقدار خرید باید بیشتر از صفر باشد."),
            ({"quantity": "abc"}, "مقدار خرید معتبر نیست."),
            ({"unit_cost": -1}, "قیمت خرید واردشده معتبر نیست."),
            ({"unit_cost": "xyz"}, "قیمت خرید معتبر نیست."),
            ({"item_id": 999999}, "کالای انتخاب‌شده وجود ندارد."),
            ({"item_id": None}, "کالای انتخاب‌شده وجود ندارد."),
        ]
        for over, message in cases:
            r = self._buy(**over)
            self.assertEqual(r.status_code, 400, (over, r.content))
            self.assertEqual(r.json()["error"]["message"], message, over)

        self.w.milk.refresh_from_db()
        self.assertEqual(float(self.w.milk.current_stock), 1000.0)
        self.assertFalse(
            InventoryTransaction.objects.filter(
                kind=InventoryTransaction.Kind.PURCHASE
            ).exists()
        )

    # ---------- ضایعات ----------
    def test_waste_decreases_stock_and_writes_ledger(self):
        r = self._waste(quantity=200, reason="spoiled", note="یخ‌زده")
        self.assertEqual(r.status_code, 201, r.content)
        j = r.json()

        self.w.milk.refresh_from_db()
        self.assertEqual(float(self.w.milk.current_stock), 800.0)

        self.assertEqual(
            (j["item_id"], j["item_name"], j["unit"], j["reason"]), 
            (self.w.milk.id, "شیر", "ml", "spoiled"),
        )
        self.assertEqual((j["quantity"], j["note"]), (200.0, "یخ‌زده"))
        self.assertIn("wasted_at", j)

        t = InventoryTransaction.objects.get(pk=j["id"])
        self.assertEqual(t.kind, InventoryTransaction.Kind.WASTE)
        self.assertEqual(float(t.quantity), -200.0)  # در دفتر حرکت علامت‌دار
        self.assertEqual(j["quantity"], 200.0)  # اما به کاربر مثبت
        self.assertEqual(t.reason, "spoiled")
        self.assertEqual(t.item_name_snapshot, "شیر")

    def test_waste_list_returns_snapshot_after_rename(self):
        wid = self._waste(quantity=100).json()["id"]
        self.api.patch(
            f"{URL}{self.w.milk.id}/",
            json.dumps({"name": "شیر کم‌چرب"}),
            content_type="application/json",
        )
        row = next(r for r in self.api.get(WASTES_URL).json() if r["id"] == wid)
        self.assertEqual(row["item_name"], "شیر")

    def test_waste_more_than_stock_is_rejected_with_nothing_written(self):
        r = self._waste(quantity=1500)
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json()["error"]["code"], "insufficient_stock")

        self.w.milk.refresh_from_db()
        self.assertEqual(float(self.w.milk.current_stock), 1000.0)  # موجودی دست‌نخورده
        self.assertFalse(
            InventoryTransaction.objects.filter(
                kind=InventoryTransaction.Kind.WASTE
            ).exists()
        )

    def test_waste_exactly_stock_is_allowed(self):
        r = self._waste(quantity=1000)
        self.assertEqual(r.status_code, 201, r.content)
        self.w.milk.refresh_from_db()
        self.assertEqual(float(self.w.milk.current_stock), 0.0)

    def test_waste_validation(self):
        cases = [
            ({"quantity": 0}, "مقدار ضایعات باید بیشتر از صفر باشد."),
            ({"quantity": -5}, "مقدار ضایعات باید بیشتر از صفر باشد."),
            ({"reason": ""}, "دلیل ضایعات را انتخاب کنید."),
            ({"reason": None}, "دلیل ضایعات را انتخاب کنید."),
            ({"reason": "invaniD"}, "دلیل ضایعات نامعتبر است."),
            ({"item_id": 999999}, "کالای انتخاب‌شده وجود ندارد."),
        ]
        for over, message in cases:
            r = self._waste(**over)
            self.assertEqual(r.status_code, 400, (over, r.content))
            self.assertEqual(r.json()["error"]["message"], message, over)

        self.w.milk.refresh_from_db()
        self.assertEqual(float(self.w.milk.current_stock), 1000.0)
        self.assertFalse(
            InventoryTransaction.objects.filter(
                kind=InventoryTransaction.Kind.WASTE
            ).exists()
        )

    def test_all_waste_reasons_accepted(self):
        for reason in InventoryTransaction.WasteReason.values:
            r = self._waste(quantity=10, reason=reason)
            self.assertEqual(r.status_code, 201, (reason, r.content))
            self.assertEqual(r.json()["reason"], reason)

    # ---------- تراکنش و هم‌زمانی ----------
    def test_select_for_update_is_used(self):
        """تغییر موجودی باید داخل قفل ردیف انجام شود."""
        for call in (
            lambda: self._buy(quantity=10),
            lambda: self._waste(quantity=10),
        ):
            seen = []
            original = QuerySet.select_for_update

            def spy(self, *a, **kw):
                seen.append(self.model.__name__)
                return original(self, *a, **kw)

            with patch.object(QuerySet, "select_for_update", spy):
                call()
            self.assertIn("InventoryItem", seen)

    def test_sequential_overdraft_second_call_rejected(self):
        """پس از کسر اول، درخواست دوم باید موجودی به‌روزشده را ببیند و رد شود."""
        self.assertEqual(self._waste(quantity=700).status_code, 201)
        r = self._waste(quantity=700)  # فقط ۳۰۰ باقی مانده
        self.assertEqual(r.status_code, 409, r.content)

        self.w.milk.refresh_from_db()
        self.assertEqual(float(self.w.milk.current_stock), 300.0)
        self.assertEqual(
            InventoryTransaction.objects.filter(
                kind=InventoryTransaction.Kind.WASTE
            ).count(),
            1,
        )

    def test_ledger_is_the_single_source_of_stock_changes(self):
        """موجودی = جمع ردیف‌های دفتر حرکات (شروع از صفر)."""
        self.w.milk.current_stock = 0
        self.w.milk.save(update_fields=["current_stock"])

        self._buy(quantity=1000, unit_cost=1000)
        self._buy(quantity=500, unit_cost=2000)
        self._waste(quantity=300, reason="damaged")

        self.w.milk.refresh_from_db()
        total = sum(
            float(t.quantity)
            for t in InventoryTransaction.objects.filter(
                item=self.w.milk, kind=InventoryTransaction.Kind.PURCHASE
            )
        ) - sum(
            abs(float(t.quantity))
            for t in InventoryTransaction.objects.filter(
                item=self.w.milk, kind=InventoryTransaction.Kind.WASTE
            )
        )
        self.assertEqual(float(self.w.milk.current_stock), total)
        self.assertEqual(float(self.w.milk.current_stock), 1200.0)
