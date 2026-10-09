import json

from django.test import Client, TestCase

from core.testing import make_world, scan
from waiter_calls.models import WaiterCall


class WaiterCallTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.api = Client(headers={"Authorization": f"Token {self.w.token}"})

    def _customer(self, table):
        c = Client()
        scan(c, table)
        return c

    def test_spam_protection_and_lifecycle(self):
        c = self._customer(self.w.t2)
        r1 = c.post("/api/customer/waiter/", "{}", content_type="application/json")
        r2 = c.post("/api/customer/waiter/", "{}", content_type="application/json")
        self.assertEqual((r1.status_code, r2.status_code), (201, 200))
        self.assertIn("ویتر به زودی", r2.json()["message"])
        self.assertEqual(WaiterCall.objects.count(), 1)
        cid = r1.json()["call"]["id"]
        self.assertEqual(
            self.api.post(f"/api/v1/waiter-calls/{cid}/acknowledge/").json()["status"],
            "acknowledged",
        )
        # هنوز فعال است → درخواست جدید ساخته نمی‌شود
        self.assertEqual(
            c.post(
                "/api/customer/waiter/", "{}", content_type="application/json"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.api.post(f"/api/v1/waiter-calls/{cid}/complete/").json()["status"],
            "completed",
        )
        # بعد از رسیدگی دوباره ممکن است
        self.assertEqual(
            c.post(
                "/api/customer/waiter/", "{}", content_type="application/json"
            ).status_code,
            201,
        )

    def test_scenario3_two_tables_independent(self):
        for t in (self.w.t2, self.w.t5):
            self._customer(t).post(
                "/api/customer/waiter/", "{}", content_type="application/json"
            )
        active = self.api.get("/api/v1/waiter-calls/?active=1").json()
        self.assertEqual(sorted(c["table_number"] for c in active), [2, 5])
        self.api.post(f"/api/v1/waiter-calls/{active[0]['id']}/acknowledge/")
        states = {
            c["table_number"]: c["status"]
            for c in self.api.get("/api/v1/waiter-calls/?active=1").json()
        }
        self.assertEqual(
            states, {2: "acknowledged", 5: "pending"}
        )  # هر میز وضعیت مستقل
        overview = {
            o["table"]["number"]: o for o in self.api.get("/api/v1/tables/").json()
        }
        self.assertEqual(overview[5]["waiter_call"]["status"], "pending")
        self.assertIsNone(overview[8]["waiter_call"])
