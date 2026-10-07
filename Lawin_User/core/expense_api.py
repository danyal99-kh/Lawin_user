"""CRUD هزینه‌ها. قالب خروجی دقیقاً مطابق Expense.fromJson در Flutter است.
تاریخ همیشه سمت سرور ثبت می‌شود و در ویرایش ثابت می‌ماند (هر date ارسالی نادیده گرفته می‌شود).

ثبت در دفتر حسابداری عمداً اینجا نوشته نشده: `core.signals` هر ذخیره/حذف
Expense را هم‌تراز می‌کند تا هیچ راهی (API، پنل ادمین، shell، تست) بدون اثر
حسابداری رد و رنگ نشود. برای دیدن پیاده‌سازی: core/signals.py
"""

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.errors import Invalid, NotFound
from core.security import HasSecurityTicket

from .models import CashAccount, Expense

# هزینه‌ها بخشی از «حسابداری»اند؛ پس مثل بقیه‌ی APIهای مالی بلیت امنیتی لازم دارند.
FINANCIAL_PERMISSIONS = [IsAdminUser, HasSecurityTicket]

CATEGORIES = {c for c, _ in Expense.CATEGORIES}
ACCOUNTS = {c for c, _ in CashAccount.choices}
MAX_TITLE, MAX_NOTE = 120, 300
MAX_AMOUNT = 10**12 - 1  # Flutter حداکثر ۱۲ رقم اجازه می‌دهد


def expense_dict(e):
    return {
        "id": e.id,
        "title": e.title,
        "amount": e.amount,
        "category": e.category,
        "account": e.account,
        "date": e.date.isoformat(),
        "note": e.note or None,  # note خالی ← null
    }


def _amount(raw):
    if isinstance(raw, bool):
        raise Invalid("مبلغ هزینه را درست وارد کنید.")
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    if isinstance(raw, str) and raw.strip().isascii() and raw.strip().isdigit():
        raw = int(raw.strip())
    if not isinstance(raw, int):
        raise Invalid("مبلغ هزینه را درست وارد کنید.")
    if raw <= 0:
        raise Invalid("مبلغ هزینه باید بیشتر از صفر باشد.")
    if raw > MAX_AMOUNT:
        raise Invalid("مبلغ واردشده معتبر نیست.")
    return raw


def _clean(data, partial):
    if not isinstance(data, dict):
        raise Invalid("درخواست نامعتبر است.")
    out = {}
    if "title" in data or not partial:
        title = data.get("title")
        if not isinstance(title, str) or not title.strip():
            raise Invalid("عنوان هزینه را وارد کنید.")
        title = title.strip()
        if len(title) > MAX_TITLE:
            raise Invalid(f"عنوان هزینه حداکثر {MAX_TITLE} حرف باشد.")
        out["title"] = title
    if "amount" in data or not partial:
        out["amount"] = _amount(data.get("amount"))
    if "category" in data:
        if data["category"] not in CATEGORIES:
            raise Invalid("دسته‌بندی هزینه نامعتبر است.")
        out["category"] = data["category"]
    elif not partial:
        out["category"] = "other"  # در ایجاد، بدون category ← other
    if "account" in data:
        if data["account"] not in ACCOUNTS:
            raise Invalid("حساب پرداخت نامعتبر است.")
        out["account"] = data["account"]
    elif not partial:
        out["account"] = CashAccount.CASH
    if "note" in data:
        note = data["note"]
        if note is None:
            note = ""
        if not isinstance(note, str):
            raise Invalid("توضیحات نامعتبر است.")
        note = note.strip()
        if len(note) > MAX_NOTE:
            raise Invalid(f"توضیحات حداکثر {MAX_NOTE} حرف باشد.")
        out["note"] = note
    return out


def _get(pk):
    try:
        return Expense.objects.get(pk=pk)
    except Expense.DoesNotExist:
        raise NotFound("هزینه پیدا نشد.")


@api_view(["GET", "POST"])
@permission_classes(FINANCIAL_PERMISSIONS)
def expenses(request):
    if request.method == "POST":
        data = _clean(request.data, partial=False)
        e = Expense.objects.create(date=timezone.now(), **data)  # سیگنال دفتر را می‌نویسد
        return Response(expense_dict(e), status=201)
    return Response([expense_dict(e) for e in Expense.objects.order_by("-date", "-id")])


@api_view(["GET", "PUT", "PATCH", "DELETE"])
@permission_classes(FINANCIAL_PERMISSIONS)
def expense_detail(request, pk):
    e = _get(pk)
    from django.db import transaction

    if request.method == "DELETE":
        with transaction.atomic():
            e.delete()  # سیگنال، ردیف دفتر را پاک می‌کند
        return Response(status=204)
    if request.method in ("PUT", "PATCH"):
        data = _clean(request.data, partial=True)
        with transaction.atomic():
            e = Expense.objects.select_for_update().get(pk=pk)
            for k, v in data.items():
                setattr(e, k, v)
            if data:
                e.save(update_fields=list(data))  # date ثابت می‌ماند؛ سیگنال دفتر را جایگزین می‌کند
    return Response(expense_dict(e))
