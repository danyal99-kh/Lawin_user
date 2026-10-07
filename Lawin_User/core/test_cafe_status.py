"""تست‌های وضعیت کافه: GET/POST /api/v1/cafe/status/

این endpoint منبع حقیقت باز/بسته بودن کافه است: GET فقط می‌خواند و POST با
`action=open|close` زمان باز/بسته شدن را ثبت می‌کند. نوشتن فقط برای ادمین
(IsAdminUser) مجاز است.
"""

import json
from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token

from core.models import CafeStatus
from core.testing import make_world

URL = "/api/v1/cafe/status/"


class CafeStatusApiTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.api = self.admin_client()

    def admin_client(self):
        """کلاینتی با توکنِ ادمین. `make_world` از قبل توکن ساخته؛ اگر (با بستن
        کافه) حذف شده باشد، یک توکن تازه ساخته می‌شود."""
        token, _ = Token.objects.get_or_create(user=self.w.admin)
        return Client(headers={"Authorization": f"Token {token.key}"})

    def send(self, method, url, body=None):
        return getattr(self.api, method)(
            url, json.dumps(body if body is not None else {}),
            content_type="application/json",
        )

    def get(self):
        return self.api.get(URL)

    def act(self, action):
        return self.send("post", URL, {"action": action})


class AuthTests(CafeStatusApiTests):
    def test_requires_token(self):
        self.assertEqual(Client().get(URL).status_code, 401)
        self.assertEqual(
            Client().post(URL, "{}", content_type="application/json").status_code,
            401,
        )

    def test_non_staff_cannot_read(self):
        from django.contrib.auth import get_user_model
        from rest_framework.authtoken.models import Token

        user = get_user_model().objects.create_user("cashier", password="x")
        anon = Client(headers={"Authorization": f"Token {Token.objects.create(user=user).key}"})
        self.assertEqual(anon.get(URL).status_code, 403)

    def test_non_staff_cannot_change_the_status(self):
        """کاربر غیرمجاز نباید با HTTP مستقیم بتواند کافه را باز/بسته کند."""
        from django.contrib.auth import get_user_model
        from rest_framework.authtoken.models import Token

        user = get_user_model().objects.create_user("cashier", password="x")
        anon = Client(headers={"Authorization": f"Token {Token.objects.create(user=user).key}"})
        r = anon.post(URL, json.dumps({"action": "open"}),
                      content_type="application/json")
        self.assertEqual(r.status_code, 403)
        self.assertFalse(CafeStatus.objects.exists())

    def test_put_and_patch_are_not_allowed(self):
        for method in ("put", "patch"):
            r = self.send(method, URL, {"action": "open"})
            self.assertEqual(r.status_code, 405, method)


class ReadTests(CafeStatusApiTests):
    def test_get_returns_default_without_creating_a_row(self):
        self.assertFalse(CafeStatus.objects.exists())
        r = self.get()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(
            r.json(),
            {"is_open": False, "opened_at": None, "closed_at": None},
        )
        # خواندنِ داشبورد نباید در دیتابیس سطر بسازد.
        self.assertFalse(CafeStatus.objects.exists())

    def test_get_is_the_source_of_truth_after_a_refresh(self):
        self.act("open")
        self.assertEqual(
            self.get().json(),
            {
                "is_open": True,
                "opened_at": CafeStatus.load().opened_at.isoformat(),
                "closed_at": None,
            },
        )

    def test_get_keeps_the_last_close_after_a_logout_login(self):
        self.act("open")
        self.act("close")
        # بستن کافه توکن را باطل می‌کند؛ توکن تازه = «ورود دوباره».
        self.api = self.admin_client()
        j = self.get().json()
        self.assertIs(j["is_open"], False)
        self.assertIsNotNone(j["closed_at"])
        self.assertIsNotNone(j["opened_at"])

    def test_timestamps_are_iso8601_with_a_utc_offset(self):
        self.act("open")
        self.act("close")
        self.api = self.admin_client()  # بستن کافه توکن را باطل کرد
        j = self.get().json()
        self.assertRegex(j["opened_at"], r"[+-]\d{2}:\d{2}$")
        self.assertRegex(j["closed_at"], r"[+-]\d{2}:\d{2}$")


class OpenTests(CafeStatusApiTests):
    def test_open_sets_the_flag_and_the_opened_at(self):
        before = timezone.now()
        r = self.act("open")
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertIs(j["is_open"], True)
        self.assertIsNotNone(j["opened_at"])
        self.assertIsNone(j["closed_at"])
        parsed = datetime.fromisoformat(j["opened_at"])
        self.assertGreaterEqual(parsed, before - timedelta(seconds=1))
        saved = CafeStatus.load()
        self.assertIs(saved.is_open, True)
        self.assertIsNotNone(saved.opened_at)

    def test_opening_a_closed_cafe_clears_the_previous_closed_at(self):
        self.act("open")
        self.act("close")
        self.api = self.admin_client()  # بستن کافه توکن را باطل کرد
        self.act("open")
        self.assertIsNone(CafeStatus.load().closed_at)

    def test_reopening_registers_a_new_opened_at(self):
        first = self.act("open").json()["opened_at"]
        self.act("close")
        self.api = self.admin_client()  # بستن کافه توکن را باطل کرد
        second = self.act("open").json()["opened_at"]
        self.assertIsNotNone(first)
        self.assertGreaterEqual(
            datetime.fromisoformat(second),
            datetime.fromisoformat(first),
        )

    def test_repeated_open_is_a_no_op(self):
        first = self.act("open")
        again = self.act("open")
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(again.json(), first.json())

    def test_only_one_row_ever_exists(self):
        for _ in range(3):
            self.act("open")
            self.act("close")
            self.api = self.admin_client()  # بستن کافه توکن را باطل کرد
        self.assertEqual(CafeStatus.objects.count(), 1)


class CloseTests(CafeStatusApiTests):
    def test_close_sets_the_flag_and_the_closed_at(self):
        self.act("open")
        r = self.act("close")
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertIs(j["is_open"], False)
        self.assertIsNotNone(j["closed_at"])
        # زمان باز شدنِ همان روز باید برای «شروع فعالیت» باقی بماند.
        self.assertIsNotNone(j["opened_at"])

    def test_closing_a_closed_cafe_is_a_no_op(self):
        first = self.act("close")
        self.assertEqual(first.status_code, 200, first.content)
        self.assertIsNone(first.json()["closed_at"])
        again = self.act("close")
        self.assertEqual(again.json(), first.json())

    def test_close_never_invents_an_opened_at(self):
        j = self.act("close").json()
        self.assertIs(j["is_open"], False)
        self.assertIsNone(j["opened_at"])
        self.assertIsNone(j["closed_at"])


class ValidationTests(CafeStatusApiTests):
    def test_unknown_action_is_rejected_and_state_is_unchanged(self):
        self.act("open")
        before = CafeStatus.load()
        for body in ({"action": "toggle"}, {"action": ""}, {"action": None}, {}):
            r = self.send("post", URL, body)
            self.assertEqual(r.status_code, 400, r.content)
            self.assertEqual(r.json()["error"]["code"], "validation")
        after = CafeStatus.load()
        self.assertIs(after.is_open, before.is_open)
        self.assertEqual(after.opened_at, before.opened_at)

    def test_a_body_that_is_not_an_object_is_rejected(self):
        r = self.send("post", URL, ["open"])
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()["error"]["code"], "validation")
        self.assertFalse(CafeStatus.objects.exists())

    def test_error_shape_matches_the_api_contract(self):
        r = self.act("nope")
        self.assertEqual(set(r.json()), {"error"})
        self.assertEqual(set(r.json()["error"]), {"code", "message"})
        self.assertEqual(r.json()["error"]["code"], "validation")
        self.assertIn("نامعتبر", r.json()["error"]["message"])


class PersistenceTests(CafeStatusApiTests):
    def test_open_then_close_survives_a_fresh_client(self):
        self.act("open")
        self.act("close")
        # توکنِ قبلی با بستن کافه باطل شده؛ ورود دوباره = توکن تازه.
        fresh = self.admin_client()
        j = fresh.get(URL).json()
        self.assertIs(j["is_open"], False)
        self.assertIsNotNone(j["closed_at"])
        self.assertIsNotNone(j["opened_at"])
