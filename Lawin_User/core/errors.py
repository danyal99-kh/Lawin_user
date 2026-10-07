from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_handler
from rest_framework.exceptions import ErrorDetail


class DomainError(Exception):
    """خطای کسب‌وکار با پیام فارسی قابل‌نمایش. کد‌ها با FailureType پنل Flutter هم‌نام‌اند."""

    status = 400
    code = "validation"

    def __init__(self, message="اطلاعات واردشده نامعتبر است."):
        super().__init__(message)
        self.message = message

    def payload(self):
        return {"error": {"code": self.code, "message": self.message}}


class Invalid(DomainError):
    status, code = 400, "validation"


class NotFound(DomainError):
    status, code = 404, "not_found"


class Conflict(DomainError):
    status, code = 409, "conflict"


class OutOfStock(DomainError):
    status, code = 409, "insufficient_stock"


class InactiveProduct(DomainError):
    status, code = 409, "inactive_product"


class TooManyRequests(DomainError):
    status, code = 429, "rate_limited"


class SecurityDenied(DomainError):
    """رمز امنیتی نادرست یا نشست امنیتی نامعتبر/منقضی.

    پیام عمداً عمومی است (مثل پیام ورود) تا وجود و درستی رمز را لو ندهد.
    """

    status, code = 403, "security_denied"

    def __init__(self):
        super().__init__("رمز امنیتی صحیح نیست.")


class SecurityNotConfigured(DomainError):
    """رمز امنیتی هنوز تنظیم نشده؛ کاربر باید اول از تنظیمات آن را بسازد."""

    status, code = 403, "security_not_configured"

    def __init__(self):
        super().__init__("رمز امنیتی هنوز تنظیم نشده است.")


FIELD_LABELS = {
    "name": "نام",
    "title": "عنوان",
    "price": "قیمت",
    "amount": "مبلغ",
    "category": "دسته‌بندی",
    "category_id": "دسته‌بندی",
    "quantity": "مقدار",
    "unit": "واحد",
    "unit_cost": "قیمت خرید",
    "min_stock": "حداقل موجودی",
    "description": "توضیحات",
    "note": "توضیحات",
    "reason": "دلیل",
}
CODE_MESSAGES = {
    "unique": "{f} تکراری است؛ این مورد قبلاً ثبت شده.",
    "required": "{f} را وارد کنید.",
    "blank": "{f} را وارد کنید.",
    "null": "{f} را وارد کنید.",
    "does_not_exist": "{f} انتخاب‌شده وجود ندارد.",
    "incorrect_type": "{f} نامعتبر است.",
    "max_length": "{f} بیش از حد طولانی است.",
    "min_value": "{f} کمتر از حد مجاز است.",
    "max_value": "{f} بیشتر از حد مجاز است.",
    "invalid": "{f} نامعتبر است.",
}


def _first_error(data, field=None):
    """اولین (فیلد، ErrorDetail) را از ساختار تودرتوی خطای DRF برمی‌گرداند."""
    if isinstance(data, ErrorDetail):
        return field, data
    if isinstance(data, dict):
        for k, v in data.items():
            found = _first_error(v, None if k == "non_field_errors" else k)
            if found:
                return found
    if isinstance(data, (list, tuple)):
        for v in data:
            found = _first_error(v, field)
            if found:
                return found
    return None


def _persian_validation_message(data):
    found = _first_error(data)
    if not found:
        return "اطلاعات واردشده نامعتبر است."
    field, err = found
    label = FIELD_LABELS.get(field, "این مورد" if field is None else "مقدار")
    template = CODE_MESSAGES.get(getattr(err, "code", ""), "{f} نامعتبر است.")
    return template.format(f=label)


def api_exception_handler(exc, context):
    """قالب یکسان خطا برای همه‌ی APIهای ادمین: {"error": {"code", "message"}}"""
    if isinstance(exc, DomainError):
        return Response(exc.payload(), status=exc.status)
    resp = drf_handler(exc, context)
    if resp is None:
        return None
    status = resp.status_code
    if status in (401, 403):
        code, message = (
            "unauthorized",
            "نشست شما منقضی شده است. لطفاً دوباره وارد شوید.",
        )
    elif status == 404:
        code, message = "not_found", "مورد درخواستی پیدا نشد."
    elif status == 400:
        code, message = "validation", _persian_validation_message(resp.data)
    else:
        code, message = "error", "درخواست نامعتبر است."
    resp.data = {"error": {"code": code, "message": message}}
    return resp
