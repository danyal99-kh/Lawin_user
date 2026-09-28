from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_handler


class DomainError(Exception):
    """خطای کسب‌وکار با پیام فارسی قابل‌نمایش. کد‌ها با FailureType پنل Flutter هم‌نام‌اند."""
    status = 400
    code = "validation"

    def __init__(self, message="اطلاعات واردشده نامعتبر است."):
        super().__init__(message)
        self.message = message

    def payload(self):
        return {"error": {"code": self.code, "message": self.message}}


class Invalid(DomainError): status, code = 400, "validation"
class NotFound(DomainError): status, code = 404, "not_found"
class Conflict(DomainError): status, code = 409, "conflict"
class OutOfStock(DomainError): status, code = 409, "insufficient_stock"
class InactiveProduct(DomainError): status, code = 409, "inactive_product"
class TooManyRequests(DomainError): status, code = 429, "rate_limited"


def api_exception_handler(exc, context):
    """قالب یکسان خطا برای همه‌ی APIهای ادمین: {"error": {"code", "message"}}"""
    if isinstance(exc, DomainError):
        return Response(exc.payload(), status=exc.status)
    resp = drf_handler(exc, context)
    if resp is None:
        return None
    detail = resp.data.get("detail") if isinstance(resp.data, dict) else None
    code = {400: "validation", 401: "unauthorized", 403: "unauthorized", 404: "not_found"}.get(
        resp.status_code, "error")
    resp.data = {"error": {"code": code, "message": str(detail) if detail else "درخواست نامعتبر است.",
                           **({"details": resp.data} if not detail else {})}}
    return resp
