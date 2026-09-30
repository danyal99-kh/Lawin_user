"""مدیریت هزینه‌ها."""

from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.errors import Invalid, NotFound

from .models import Expense

MAX_TITLE_LEN = 120
VALID_CATEGORIES = {c[0] for c in Expense.CATEGORIES}


def expense_dict(e):
    return {
        "id": e.id,
        "title": e.title,
        "amount": e.amount,
        "category": e.category,
        "date": e.date.isoformat(),
        "note": e.note or None,
    }


def _validate(data):
    title = str(data.get("title") or "").strip()
    if not title:
        raise Invalid("عنوان هزینه را وارد کنید.")
    if len(title) > MAX_TITLE_LEN:
        raise Invalid(f"عنوان هزینه حداکثر {MAX_TITLE_LEN} حرف باشد.")
    try:
        amount = int(data.get("amount"))
    except (TypeError, ValueError):
        raise Invalid("مبلغ هزینه معتبر نیست.")
    if amount <= 0:
        raise Invalid("مبلغ هزینه باید بیشتر از صفر باشد.")
    category = data.get("category")
    if category not in VALID_CATEGORIES:
        raise Invalid("دسته‌بندی هزینه نامعتبر است.")
    note = str(data.get("note") or "").strip()[:500]
    return title, amount, category, note


def list_expenses():
    return Expense.objects.order_by("-date")


@transaction.atomic
def create_expense(data):
    title, amount, category, note = _validate(data)
    return Expense.objects.create(
        title=title, amount=amount, category=category, date=timezone.now(), note=note
    )


@transaction.atomic
def update_expense(pk, data):
    try:
        e = Expense.objects.select_for_update().get(pk=pk)
    except Expense.DoesNotExist:
        raise NotFound("هزینه پیدا نشد.")
    title, amount, category, note = _validate(data)
    e.title, e.amount, e.category, e.note = title, amount, category, note
    e.save(update_fields=["title", "amount", "category", "note"])
    return e


@transaction.atomic
def delete_expense(pk):
    try:
        e = Expense.objects.get(pk=pk)
    except Expense.DoesNotExist:
        raise NotFound("هزینه پیدا نشد.")
    e.delete()


@api_view(["GET", "POST"])
def expenses(request):
    if request.method == "POST":
        e = create_expense(request.data)
        return Response(expense_dict(e), status=201)
    return Response([expense_dict(e) for e in list_expenses()])


@api_view(["PATCH", "PUT", "DELETE"])
def expense_detail(request, pk):
    if request.method == "DELETE":
        delete_expense(pk)
        return Response(status=204)
    e = update_expense(pk, request.data)
    return Response(expense_dict(e))
