"""API نسیه: فهرست طلب‌ها، بدهکارها و ثبت تسویه.

دسترسی فقط ادمینِ واردشده لازم است؛ به درخواست کاربر، این بخش رمز امنیتی
دوم نمی‌خواهد (برخلاف حسابداری/هزینه‌ها). هیچ عددی از Client برای مانده یا
وضعیت پذیرفته نمی‌شود؛ همه‌چیز از خودِ ردیف و دفتر محاسبه می‌شود.
"""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.errors import Invalid, NotFound

from . import services
from .models import Credit, CreditPayment, Debtor
from .serializers import credit_dict, debtor_dict, payment_dict

STATUS_VALUES = {c for c, _ in Credit.Status.choices}
MAX_QUERY = 60


@api_view(["GET"])
@permission_classes([IsAdminUser])
def credits(request):
    """GET /credits/?debtor=<id>&status=open — فهرست نسیه‌ها (قدیمی‌ترین اول)."""
    qs = Credit.objects.select_related("debtor", "order").order_by(
        "created_at", "id"
    )
    q = request.query_params
    if q.get("debtor"):
        try:
            qs = qs.filter(debtor_id=int(q["debtor"]))
        except (TypeError, ValueError):
            raise Invalid("بدهکار نامعتبر است.")
    status = q.get("status")
    if status:
        if status not in STATUS_VALUES:
            raise Invalid("وضعیت نسیه نامعتبر است.")
        qs = qs.filter(status=status)
    return Response([credit_dict(c) for c in qs])


@api_view(["GET", "POST"])
@permission_classes([IsAdminUser])
def debtors(request):
    """GET /credits/debtors/?q= — بدهکارها با جمع مانده‌شان.
    POST — ساخت دستی بدهکار (معمولاً خودکار هنگام پرداخت نسیه ساخته می‌شود).
    """
    if request.method == "POST":
        data = request.data if isinstance(request.data, dict) else {}
        debtor = services.get_or_create_debtor(
            name=data.get("name"), phone=data.get("phone"), note=data.get("note")
        )
        return Response(debtor_dict(debtor, **services.debtor_totals_for(debtor)),
                        status=201)
    qs = services.debtor_totals()
    query = str(request.query_params.get("q") or "").strip()[:MAX_QUERY]
    if query:
        qs = qs.filter(name__icontains=query)
    return Response(
        [
            debtor_dict(
                d,
                debt=d.debt or 0,
                open_credits=d.open_credits or 0,
                extended=d.extended or 0,
                last_activity=d.last_activity,
            )
            for d in qs
        ]
    )


@api_view(["GET"])
@permission_classes([IsAdminUser])
def debtor_detail(request, pk):
    """GET /credits/debtors/<pk>/ — جزئیات یک بدهکار + نسیه‌هایش."""
    try:
        debtor = Debtor.objects.get(pk=pk)
    except (Debtor.DoesNotExist, ValueError, TypeError):
        raise NotFound("بدهکار پیدا نشد.")
    totals = services.debtor_totals_for(debtor)
    return Response(
        {
            "debtor": debtor_dict(debtor, **totals),
            "credits": [
                credit_dict(c)
                for c in Credit.objects.filter(debtor=debtor)
                .select_related("debtor", "order")
                .order_by("created_at", "id")
            ],
        }
    )


@api_view(["GET", "POST"])
@permission_classes([IsAdminUser])
def payments(request):
    """GET /credits/payments/?debtor=<id> — وصول‌های ثبت‌شده.
    POST — ثبت تسویه (قدیمی‌ترین نسیه اول؛ بیش از مانده رد می‌شود).
    """
    if request.method == "POST":
        data = request.data if isinstance(request.data, dict) else {}
        debtor_id = _int_or_none(data.get("debtor_id"))
        credit_id = _int_or_none(data.get("credit_id"))
        if debtor_id is None and credit_id is None:
            raise Invalid("بدهکار یا نسیه را مشخص کنید.")
        result = services.settle(
            amount=data.get("amount"),
            account=data.get("account"),
            debtor_id=debtor_id,
            credit_id=credit_id,
            user=request.user,
            idempotency_key=data.get("idempotency_key"),
            note=data.get("note"),
        )
        return Response(result, status=201)

    qs = CreditPayment.objects.select_related(
        "credit", "credit__debtor", "credit__order"
    ).order_by("-created_at", "-id")
    q = request.query_params
    if q.get("debtor"):
        debtor_id = _int_or_none(q["debtor"])
        if debtor_id is None:
            raise Invalid("بدهکار نامعتبر است.")
        qs = qs.filter(credit__debtor_id=debtor_id)
    if q.get("credit"):
        credit_id = _int_or_none(q["credit"])
        if credit_id is None:
            raise Invalid("نسیه نامعتبر است.")
        qs = qs.filter(credit_id=credit_id)
    return Response([payment_dict(p) for p in qs[:500]])


def _int_or_none(raw):
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise Invalid("شناسه نامعتبر است.")
