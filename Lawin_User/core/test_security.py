"""تست‌های رمز امنیتی بخش‌های مالی و گیتِ endpointهای /accounting و /reports.

اهداف: رمز فقط هش (PBKDF2) ذخیره شود، «ورود امنیتی» در Backend اجباری باشد،
بلیت کوتاه‌مدت/لغزان باشد، تغییر رمز همه‌ی بلیت‌های قبلی را باطل کند، بستن
کافه و Logout توکن نشست را سمت سرور ببندند و نرخ‌ها محدود باشند.
"""

import json

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.test import Client, TestCase

from core.models import SecuritySettings
from core.security import TICKET_HEADER
from core.testing import make_world
from rest_framework.authtoken.models import Token

VERIFY_URL = "/api/v1/security/verify/"
PASSWORD_URL = "/api/v1/security/password/"
TRANSACTIONS_URL = "/api/v1/accounting/transactions/"
REPORTS_URL = "/api/v1/reports/"
EXPENSES_URL = "/api/v1/accounting/expenses/"
SETTINGS_URL = "/api/v1/settings/"
LOGIN_URL = "/api/v1/auth/login/"
LOGOUT_URL = "/api/v1/auth/logout/"
CAFE_STATUS_URL = "/api/v1/cafe/status/"

PASS = "sup3r-secret"

User = get_user_model()


def json_body(value):
    return json.dumps(value or {})


class SecurityApiTests(TestCase):
    def setUp(self):
        # در تست، هر تست ادمینی با id=1 می‌سازد (در SQLite rollback داده، اما
        # cache throttle بین تست‌ها می‌ماند). برای اینکه سهمیه‌ی نرخ محدودیت در
        # طول اجرای سویت جمع نشود، cache را در ابتدای هر تست خالی می‌کنیم.
        cache.clear()
        self.w = make_world()
        self.token = self.w.token
        self.api = Client(headers={"Authorization": f"Token {self.token}"})

    # ------------------------------------------------------------- ابزارک
    def post(self, client, url, body=None):
        return client.post(url, json_body(body), content_type="application/json")

    def get(self, client, url):
        return client.get(url)

    def verify(self, password=PASS, client=None, **body):
        return self.post(client or self.api, VERIFY_URL, {"password": password, **body})

    def unlock(self, password=PASS, client=None):
        r = self.verify(password, client)
        assert r.status_code == 200, r.content
        return r.json()["ticket"]

    def change_password(self, client=None, **body):
        return self.post(client or self.api, PASSWORD_URL, body)

    def transaction(self, ticket=None):
        headers = {}
        if ticket:
            headers[TICKET_HEADER] = ticket
        return Client(headers={**headers, "Authorization": f"Token {self.token}"}).get(
            TRANSACTIONS_URL
        )


# ----------------------------------------------------------- ذخیره‌سازی رمز
class HashingTests(SecurityApiTests):
    def test_not_configured_by_default(self):
        self.assertFalse(SecuritySettings.current().is_configured)
        self.assertFalse(SecuritySettings.objects.exists())

    def test_password_is_stored_only_as_a_pbkdf2_hash(self):
        self.change_password(new_password=PASS)
        s = SecuritySettings.load()
        self.assertTrue(s.is_configured)
        self.assertNotIn(
            PASS, s.security_hash, "متن رمز نباید در هش یا دیتابیس باشد"
        )
        self.assertTrue(
            s.security_hash.startswith("pbkdf2"),
            f"الگوریتم باید PBKDF2 باشد، نه {s.security_hash.split('$')[0]}",
        )

    def test_only_one_row_ever_exists(self):
        self.change_password(new_password=PASS)
        self.change_password(new_password=PASS + "2")
        self.assertEqual(SecuritySettings.objects.count(), 1)


# ------------------------------------------------------------- ورود امنیتی
class VerifyTests(SecurityApiTests):
    def test_requires_authentication(self):
        self.assertEqual(self.post(Client(), VERIFY_URL).status_code, 401)

    def test_reports_not_configured_state(self):
        r = self.verify(PASS)
        self.assertEqual(r.status_code, 403, r.content)
        self.assertEqual(r.json()["error"]["code"], "security_not_configured")

    def test_wrong_password_is_denied_generically(self):
        self.change_password(new_password=PASS)
        for wrong in ("wrong", "", None):
            r = self.verify(wrong)
            self.assertEqual(r.status_code, 403, wrong)
            self.assertEqual(r.json(), {
                "error": {"code": "security_denied", "message": "رمز امنیتی صحیح نیست."},
            }, wrong)

    def test_unknown_password_versus_a_wrong_password_are_indistinguishable(self):
        """هر دو پاسخ باید یکسان باشند تا وجود/درستی رمز لو نرود."""
        self.change_password(new_password=PASS)
        a = self.verify("not-the-pass").content
        b = self.verify("also-not-the-pass").content
        c = self.verify(None).content  # خالی
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_correct_password_returns_a_ticket(self):
        self.change_password(new_password=PASS)
        r = self.verify(PASS)
        self.assertEqual(r.status_code, 200, r.content)
        ticket = r.json()["ticket"]
        self.assertTrue(ticket)
        # بلیت امضای صحیح و زمان‌دار است.
        payload = signing.TimestampSigner(key="lawin-security-ticket").unsign_object(ticket, max_age=1800)
        self.assertEqual(payload["p"], "financial")
        self.assertEqual(payload["u"], self.w.admin.id)

    def test_changing_password_keeps_old_passed_and_new_passed(self):
        self.change_password(new_password=PASS)
        self.change_password(current_password=PASS, new_password=PASS + "2")
        self.assertEqual(self.verify(PASS).status_code, 403)
        self.assertEqual(self.verify(PASS + "2").status_code, 200)


# --------------------------------------------------------- تنظیم/تغییر رمز
class ChangePasswordTests(SecurityApiTests):
    def test_requires_authentication(self):
        self.assertEqual(self.post(Client(), PASSWORD_URL).status_code, 401)

    def test_first_time_setup_works_without_current(self):
        r = self.change_password(new_password=PASS)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(SecuritySettings.load().is_configured)
        self.assertTrue(SecuritySettings.load().security_hash.startswith("pbkdf2"))

    def test_too_short_password_is_rejected(self):
        for short in ("", "1", "12345"):
            r = self.change_password(new_password=short)
            self.assertEqual(r.status_code, 400, short)
            self.assertEqual(r.json()["error"]["code"], "validation")
        self.assertFalse(SecuritySettings.current().is_configured)

    def test_non_string_password_is_rejected(self):
        r = self.change_password(new_password=123456)
        self.assertEqual(r.status_code, 400)

    def test_requires_current_password_when_configured(self):
        self.change_password(new_password=PASS)
        r = self.change_password(current_password=None, new_password=PASS + "2")
        self.assertEqual(r.status_code, 403, r.content)
        # رمز قبلی هنوز برقرار است.
        self.assertEqual(self.verify(PASS).status_code, 200)

    def test_wrong_current_password_is_denied(self):
        self.change_password(new_password=PASS)
        r = self.change_password(current_password="nope", new_password=PASS + "2")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.verify(PASS + "2").status_code, 403)

    def test_change_with_correct_current_updates_the_password(self):
        self.change_password(new_password=PASS)
        r = self.change_password(current_password=PASS, new_password=PASS + "2")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.verify(PASS).status_code, 403)
        self.assertEqual(self.verify(PASS + "2").status_code, 200)


# ------------------------------------------------------ گیت endpointهای مالی
class FinancialGateTests(SecurityApiTests):
    def setUp(self):
        super().setUp()
        self.change_password(new_password=PASS)

    def test_financial_endpoints_require_a_ticket(self):
        for url in (TRANSACTIONS_URL, REPORTS_URL, EXPENSES_URL):
            r = self.get(self.api, url)
            self.assertEqual(r.status_code, 403, url)
            self.assertEqual(r.json()["error"]["code"], "security_denied", url)

    def test_valid_ticket_opens_the_financial_endpoints(self):
        ticket = self.unlock()
        for url in (TRANSACTIONS_URL, REPORTS_URL, EXPENSES_URL):
            r = self.get(
                Client(headers={
                    **{"Authorization": f"Token {self.token}"},
                    TICKET_HEADER: ticket,
                }),
                url,
            )
            self.assertEqual(r.status_code, 200, url)

    def test_garbage_and_tampered_tickets_are_rejected(self):
        ticket = self.unlock()
        for bad in ("garbage", ticket[:-3] + "xxx", ""):
            r = self.transaction(bad)
            self.assertEqual(r.status_code, 403, bad)
            self.assertEqual(r.json()["error"]["code"], "security_denied")

    def test_ticket_is_bound_to_the_admin_who_unlocked(self):
        other = User.objects.create_user("cashier2", password="x", is_staff=True)
        other_token = Token.objects.create(user=other).key
        ticket = self.unlock(client=self.api)
        r = Client(
            headers={
                "Authorization": f"Token {other_token}",
                TICKET_HEADER: ticket,
            }
        ).get(TRANSACTIONS_URL)
        self.assertEqual(r.status_code, 403, r.content)

    def test_changing_password_invalidates_already_issued_tickets(self):
        ticket = self.unlock()
        self.assertEqual(self.transaction(ticket).status_code, 200)
        self.change_password(current_password=PASS, new_password=PASS + "2")
        r = self.transaction(ticket)
        self.assertEqual(r.status_code, 403, r.content)
        # با رمز جدید همه‌چیز دوباره باز می‌شود.
        new_ticket = self.unlock(PASS + "2")
        self.assertEqual(self.transaction(new_ticket).status_code, 200)

    def test_security_endpoints_themselves_do_not_need_a_ticket(self):
        # آدمینِ لاگین‌شده بدون هیچ بلیتی باید بتواند رمز را تنظیم/تأیید کند.
        self.assertEqual(self.verify(PASS).status_code, 200)

    def test_sliding_ticket_is_refreshed_on_each_financial_response(self):
        ticket = self.unlock()
        r = self.transaction(ticket)
        self.assertEqual(r.status_code, 200)
        refreshed = r.headers.get(TICKET_HEADER)
        self.assertTrue(refreshed, "پاسخ باید بلیت تازه در هدر بدهد")
        # بلیتِ برگشتی باید همچنان معتبر باشد (نشست لغزان ادامه دارد).
        self.assertEqual(self.transaction(refreshed).status_code, 200)


# ------------------------------------------------- بستن کافه = پایانِ نشست
class CloseRevokesSessionTests(SecurityApiTests):
    def test_closing_the_cafe_revokes_the_sessions_token(self):
        self.post(self.api, CAFE_STATUS_URL, {"action": "open"})
        r = self.post(self.api, CAFE_STATUS_URL, {"action": "close"})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()["is_open"])
        self.assertEqual(self.get(self.api, SETTINGS_URL).status_code, 401)

    def test_opening_the_cafe_does_not_revoke_the_token(self):
        self.post(self.api, CAFE_STATUS_URL, {"action": "open"})
        self.assertEqual(self.get(self.api, SETTINGS_URL).status_code, 200)


# ------------------------------------------------- logout
class LogoutTests(SecurityApiTests):
    def test_logout_deletes_the_token_server_side(self):
        self.assertEqual(self.get(self.api, SETTINGS_URL).status_code, 200)
        r = self.post(self.api, LOGOUT_URL)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.get(self.api, SETTINGS_URL).status_code, 401)
        self.assertFalse(Token.objects.filter(key=self.token).exists())

    def test_logout_is_idempotent_after_the_token_is_already_gone(self):
        self.post(self.api, LOGOUT_URL)
        # توکن حذف شد؛ درخواستِ بعدی مثل بقیه‌ی APIها 401 است.
        self.assertEqual(self.post(self.api, LOGOUT_URL).status_code, 401)

    def test_login_issues_a_new_token_after_logout(self):
        # اول خارج می‌شویم تا توکن قبلی سمت سرور حذف شود.
        self.assertEqual(self.post(self.api, LOGOUT_URL).status_code, 200)
        r = self.post(Client(), LOGIN_URL, {"username": "admin", "password": "x"})
        self.assertEqual(r.status_code, 200, r.content)
        new_key = r.json()["token"]
        self.assertNotEqual(new_key, self.token)
        # توکن جدید واقعاً کار می‌کند.
        self.assertEqual(
            Client(headers={"Authorization": f"Token {new_key}"}).get(SETTINGS_URL).status_code,
            200,
        )

    def test_login_errors_are_generic_and_identical(self):
        anon = Client()
        wrong_password = self.post(anon, LOGIN_URL, {"username": "admin", "password": "bad"})
        unknown_user = self.post(anon, LOGIN_URL, {"username": "no-such-user", "password": "bad"})
        self.assertEqual(wrong_password.status_code, unknown_user.status_code)
        self.assertEqual(wrong_password.content, unknown_user.content)
        self.assertEqual(wrong_password.json()["error"]["code"], "unauthorized")

    def test_non_staff_cannot_login_to_the_admin_panel(self):
        User.objects.create_user("cashier", password="x")
        r = self.post(Client(), LOGIN_URL, {"username": "cashier", "password": "x"})
        self.assertEqual(r.status_code, 401, r.content)


# ------------------------------------------------- محدودیت نرخ
class RateLimitTests(SecurityApiTests):
    def test_security_verify_is_rate_limited(self):
        from core import security as sec

        sec.set_password(PASS)  # مستقیم، تا سهمیه‌ی HTTP مصرف نشود.
        # استاندارد 10/min؛ پس از ۱۰ تلاش، یازدهمین باید 429 شود.
        codes = [self.verify("wrong").status_code for _ in range(11)]
        self.assertEqual(codes.count(429), 1, codes)
        self.assertEqual(codes[-1], 429)