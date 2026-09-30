import json
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from core.models import Expense
from core.testing import make_world


class ExpenseApiTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.api = Client(headers={"Authorization": f"Token {self.w.token}"})

    def send(self, method, url, body=None):
        return getattr(self.api, method)(
            url, json.dumps(body or {}), content_type="application/json"
        )

    def create(self, **kw):
        body = {
            "title": "خرید شیر",
            "amount": 850000,
            "category": "raw_materials",
            "note": "",
        }
        body.update(kw)
        return self.send("post", "/api/v1/accounting/expenses/", body)

    def test_requires_token(self):
        self.assertEqual(Client().get("/api/v1/accounting/expenses/").status_code, 401)
        self.assertEqual(
            Client().delete("/api/v1/accounting/expenses/1/").status_code, 401
        )

    def test_create_server_date_and_shape(self):
        old = (timezone.now() - timedelta(days=30)).isoformat()
        r = self.create(date=old)  # date کلاینت نادیده گرفته می‌شود
        self.assertEqual(r.status_code, 201, r.content)
        j = r.json()
        self.assertIsInstance(j["amount"], int)
        self.assertEqual(
            (j["title"], j["category"], j["note"]), ("خرید شیر", "raw_materials", None)
        )
        e = Expense.objects.get(pk=j["id"])
        self.assertLess(timezone.now() - e.date, timedelta(minutes=1))

    def test_defaults_and_trim(self):
        r = self.send(
            "post",
            "/api/v1/accounting/expenses/",
            {"title": "  چای  ", "amount": "1500", "note": "  x  "},
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(
            (
                r.json()["title"],
                r.json()["category"],
                r.json()["amount"],
                r.json()["note"],
            ),
            ("چای", "other", 1500, "x"),
        )

    def test_bad_payloads(self):
        bad = [
            {"title": "", "amount": 10},
            {"title": "  ", "amount": 10},
            {"amount": 10},
            {"title": "a"},
            {"title": "a", "amount": 0},
            {"title": "a", "amount": -5},
            {"title": "a", "amount": True},
            {"title": "a", "amount": 10.5},
            {"title": "a", "amount": "abc"},
            {"title": "a", "amount": 10**13},
            {"title": "a" * 121, "amount": 10},
            {"title": "a", "amount": 10, "category": "nope"},
            {"title": "a", "amount": 10, "note": "n" * 301},
            {"title": "a", "amount": 10, "note": 5},
        ]
        for body in bad:
            r = self.send("post", "/api/v1/accounting/expenses/", body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(r.json()["error"]["code"], "validation")
        self.assertEqual(Expense.objects.count(), 0)

    def test_list_newest_first(self):
        now = timezone.now()
        Expense.objects.create(title="قدیمی", amount=1, date=now - timedelta(days=2))
        Expense.objects.create(title="جدید", amount=2, date=now)
        r = self.api.get("/api/v1/accounting/expenses/")
        self.assertEqual([x["title"] for x in r.json()], ["جدید", "قدیمی"])

    def test_patch_keeps_date_and_clears_note(self):
        e = Expense.objects.create(
            title="a",
            amount=5,
            category="rent",
            note="old",
            date=timezone.now() - timedelta(days=3),
        )
        before = e.date
        r = self.send(
            "patch",
            f"/api/v1/accounting/expenses/{e.id}/",
            {
                "title": "b",
                "amount": 99,
                "category": "water",
                "note": "",
                "date": "2000-01-01T00:00:00Z",
            },
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(
            (
                r.json()["title"],
                r.json()["amount"],
                r.json()["category"],
                r.json()["note"],
            ),
            ("b", 99, "water", None),
        )
        e.refresh_from_db()
        self.assertEqual(e.date, before)

    def test_partial_patch_and_invalid_patch(self):
        e = Expense.objects.create(title="a", amount=5, date=timezone.now())
        self.assertEqual(
            self.send(
                "patch", f"/api/v1/accounting/expenses/{e.id}/", {"amount": 7}
            ).json()["title"],
            "a",
        )
        self.assertEqual(
            self.send(
                "patch", f"/api/v1/accounting/expenses/{e.id}/", {"amount": 0}
            ).status_code,
            400,
        )
        e.refresh_from_db()
        self.assertEqual(e.amount, 7)

    def test_delete_and_404(self):
        e = Expense.objects.create(title="a", amount=5, date=timezone.now())
        self.assertEqual(
            self.api.delete(f"/api/v1/accounting/expenses/{e.id}/").status_code, 204
        )
        self.assertFalse(Expense.objects.exists())
        r = self.api.delete(f"/api/v1/accounting/expenses/{e.id}/")
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (404, "not_found"))
        self.assertEqual(
            self.send(
                "patch", "/api/v1/accounting/expenses/999/", {"amount": 1}
            ).status_code,
            404,
        )
