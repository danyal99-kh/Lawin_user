"""تست‌های تنظیمات: GET/PATCH /api/v1/settings/ و GET/PUT/PATCH /api/v1/settings/welcome/

هر دو تنظیم singleton هستند (pk=1) و PATCH باید واقعاً جزئی باشد: فیلدی که در
بدنه نیامده نباید تغییر کند.
"""

import json

from django.test import Client, TestCase

from core.models import CafeSettings, WelcomeMessage
from core.testing import make_world

URL = "/api/v1/settings/"
WELCOME_URL = "/api/v1/settings/welcome/"


class SettingsApiTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.api = Client(headers={"Authorization": f"Token {self.w.token}"})

    def send(self, method, url, body=None):
        return getattr(self.api, method)(
            url, json.dumps(body or {}), content_type="application/json"
        )

    def patch(self, **body):
        return self.send("patch", URL, body)


class AuthTests(SettingsApiTests):
    def test_requires_token(self):
        for url in (URL, WELCOME_URL):
            self.assertEqual(Client().get(url).status_code, 401, url)
            self.assertEqual(
                Client().patch(url, "{}", content_type="application/json").status_code,
                401,
                url,
            )

    def test_non_staff_is_forbidden(self):
        from django.contrib.auth import get_user_model
        from rest_framework.authtoken.models import Token

        user = get_user_model().objects.create_user("cashier", password="x")
        anon = Client(headers={"Authorization": f"Token {Token.objects.create(user=user).key}"})
        self.assertEqual(anon.get(URL).status_code, 403)


class CafeSettingsTests(SettingsApiTests):
    def test_get_creates_singleton_row_with_defaults(self):
        self.assertFalse(CafeSettings.objects.exists())
        r = self.api.get(URL)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(
            r.json(),
            {
                "name": "کافه‌کتاب",
                "address": "",
                "phone": "",
                "receipt_note": "",
                "auto_print": True,
                "low_stock_alert": True,
                "updated_at": CafeSettings.objects.get(pk=1).updated_at.isoformat(),
            },
        )
        self.assertEqual(CafeSettings.objects.count(), 1)

    def test_load_is_always_the_same_row(self):
        first = CafeSettings.load()
        first.name = "کافه دوم"
        first.save()
        again = CafeSettings.load()
        self.assertEqual(again.pk, first.pk)
        self.assertEqual(again.name, "کافه دوم")
        self.assertEqual(CafeSettings.objects.count(), 1)

    def test_save_cannot_create_a_second_row(self):
        CafeSettings.load()
        other = CafeSettings(name="رقیب")
        other.save()
        self.assertEqual(other.pk, 1)
        self.assertEqual(CafeSettings.objects.count(), 1)
        self.assertEqual(CafeSettings.load().name, "رقیب")

    def test_patch_saves_and_trims(self):
        r = self.patch(name="  کافه‌کتاب  ", address=" خیابان ولیعصر ", phone="0211234")
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertEqual((j["name"], j["address"], j["phone"]),
                         ("کافه‌کتاب", "خیابان ولیعصر", "0211234"))
        self.assertEqual(CafeSettings.load().name, "کافه‌کتاب")

    def test_patch_is_partial(self):
        self.patch(name="کافه‌کتاب", address="نشانی", receipt_note="با تشکر")
        r = self.patch(phone="0215555")
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertEqual(j["phone"], "0215555")
        # فیلدهای نیامده نباید ریست شوند
        self.assertEqual(j["name"], "کافه‌کتاب")
        self.assertEqual(j["address"], "نشانی")
        self.assertEqual(j["receipt_note"], "با تشکر")
        self.assertIs(j["auto_print"], True)
        self.assertIs(j["low_stock_alert"], True)

    def test_patch_booleans(self):
        r = self.patch(auto_print=False, low_stock_alert=False)
        self.assertIs(r.json()["auto_print"], False)
        self.assertIs(r.json()["low_stock_alert"], False)
        saved = CafeSettings.load()
        self.assertIs(saved.auto_print, False)
        self.assertIs(saved.low_stock_alert, False)

    def test_patch_rejects_non_bool(self):
        r = self.patch(auto_print="yes")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()["error"]["code"], "validation")
        self.assertIs(CafeSettings.load().auto_print, True)

    def test_patch_rejects_blank_name(self):
        r = self.patch(name="   ")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()["error"]["message"], "نام کافه را وارد کنید.")

    def test_patch_truncates_to_column_length(self):
        r = self.patch(name="ک" * 300, address="ا" * 900, receipt_note="ب" * 400)
        self.assertEqual(len(r.json()["name"]), 120)
        self.assertEqual(len(r.json()["address"]), 400)
        self.assertEqual(len(r.json()["receipt_note"]), 150)

    def test_null_text_field_becomes_empty_string(self):
        CafeSettings.load().address = "نشانی قبلی"
        CafeSettings.load().save()
        r = self.patch(address=None)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["address"], "")
        self.assertEqual(CafeSettings.load().address, "")

    def test_updated_at_moves_on_change(self):
        first = self.api.get(URL).json()["updated_at"]
        self.patch(name="کافه‌کتاب جدید")
        self.assertNotEqual(self.api.get(URL).json()["updated_at"], first)

    def test_put_is_not_allowed(self):
        r = self.send("put", URL, {"name": "کافه‌کتاب"})
        self.assertEqual(r.status_code, 405, r.content)

    def test_save_is_returned_from_db(self):
        self.patch(name="کافه‌کتاب جدید", receipt_note="یادداشت")
        j = self.api.get(URL).json()
        self.assertEqual(j["name"], "کافه‌کتاب جدید")
        self.assertEqual(j["receipt_note"], "یادداشت")


class WelcomeSettingsTests(SettingsApiTests):
    def test_default_shape_is_preserved(self):
        j = self.api.get(WELCOME_URL).json()
        self.assertEqual(set(j), {"title", "message", "enabled", "updated_at"})
        self.assertIs(j["enabled"], True)
        self.assertIn("خوش آمدید", j["title"])

    def test_patch_is_partial_and_trims(self):
        r = self.send("patch", WELCOME_URL, {"message": "  لطفاً بنشینید  "})
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertEqual(j["message"], "لطفاً بنشینید")
        self.assertEqual(j["title"], WelcomeMessage.load().title)

    def test_toggle_enabled(self):
        j = self.send("patch", WELCOME_URL, {"enabled": False}).json()
        self.assertIs(j["enabled"], False)
        self.assertIs(WelcomeMessage.load().enabled, False)

    def test_put_replaces_everything(self):
        r = self.send("put", WELCOME_URL,
                      {"title": "  عنوان تازه  ", "message": "متن تازه", "enabled": False})
        j = r.json()
        self.assertEqual((j["title"], j["message"], j["enabled"]),
                         ("عنوان تازه", "متن تازه", False))

    def test_rejects_blank_title(self):
        r = self.send("patch", WELCOME_URL, {"title": "  "})
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()["error"]["message"], "عنوان خوشامدگویی را وارد کنید.")

    def test_rejects_non_bool_enabled(self):
        r = self.send("patch", WELCOME_URL, {"enabled": "بله"})
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIs(WelcomeMessage.load().enabled, True)

    def test_welcome_is_separate_row_from_cafe_settings(self):
        self.patch(name="کافه‌کتاب")
        self.send("patch", WELCOME_URL, {"message": "خوشامدگویی تازه"})
        self.assertEqual(WelcomeMessage.load().message, "خوشامدگویی تازه")
        self.assertEqual(CafeSettings.load().name, "کافه‌کتاب")
