"""API مهمان‌ها: گزارش گروهی، جزئیات یک نام و لغو ثبت.

دسترسی فقط ادمینِ واردشده لازم است؛ به درخواست کاربر، این بخش (مثل نسیه)
رمز امنیتی دوم نمی‌خواهد. هیچ مبلغ یا وضعیتی از Client پذیرفته نمی‌شود؛
همه‌چیز از سفارش و دفتر محاسبه می‌شود.

ثبت مهمان از مسیر پرداخت انجام می‌شود (`POST /orders/{id}/pay/` یا
`POST /tables/{id}/pay/` با `method: "guest"` و `guest_name`) تا با کاری که
اپراتور از دکمه‌ی پرداخت انجام می‌دهد یکی باشد.
"""

from django.db.models import Count, Sum
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.errors import Invalid, NotFound

from . import services
from .models import Guest
from .serializers import guest_dict, group_dict, summary_dict

MAX_QUERY = 60


@api_view(["GET"])
@permission_classes([IsAdminUser])
def guests(request):
    """GET /guests/?q= — گروه مهمان‌ها بر اساس نام، به ترتیب بیشترین مبلغ."""
    qs = Guest.objects.select_related("order", "order__table")
    groups = services.guest_groups(qs)
    query = str(request.query_params.get("q") or "").strip()[:MAX_QUERY]
    if query:
        groups = groups.filter(name__icontains=query)
    return Response([group_dict(r) for r in groups])


@api_view(["GET"])
@permission_classes([IsAdminUser])
def summary(request):
    """GET /guests/summary/ — جمع کل مهمان‌نوازی و تعداد ثبت‌ها."""
    active = Guest.objects.filter(status=Guest.Status.ACTIVE)
    agg = active.aggregate(total_amount=Sum("amount"), count=Count("id"))
    return Response(
        summary_dict(
            total_amount=agg["total_amount"] or 0,
            guest_count=agg["count"] or 0,
            order_count=agg["count"] or 0,
        )
    )


@api_view(["GET"])
@permission_classes([IsAdminUser])
def guest_orders(request):
    """GET /guests/orders/?name= — سفارش‌های مهمان‌شده با یک نام مشخص."""
    name = request.query_params.get("name")
    rows = services.guest_orders(name)
    return Response([guest_dict(g) for g in rows])


@api_view(["GET", "PATCH"])
@permission_classes([IsAdminUser])
def guest_detail(request, pk):
    """GET/PATCH /guests/<pk>/ — مشاهده یا تغییر نام یک ثبت مهمان."""
    if request.method == "PATCH":
        data = request.data if isinstance(request.data, dict) else {}
        if "name" not in data:
            raise Invalid("نام مهمان را وارد کنید.")
        services.rename_guest(guest_id=pk, name=data.get("name"), user=request.user)
    try:
        guest = Guest.objects.select_related("order", "order__table").get(pk=pk)
    except (Guest.DoesNotExist, ValueError, TypeError):
        raise NotFound("مهمان پیدا نشد.")
    return Response(guest_dict(guest))


@api_view(["POST"])
@permission_classes([IsAdminUser])
def guest_cancel(request, pk):
    """POST /guests/<pk>/cancel/ — لغو مهمان‌نوازی (سند و موجودی برمی‌گردد)."""
    services.cancel_guest(guest_id=pk, user=request.user)
    try:
        guest = Guest.objects.select_related("order", "order__table").get(pk=pk)
    except (Guest.DoesNotExist, ValueError, TypeError):
        raise NotFound("مهمان پیدا نشد.")
    return Response(guest_dict(guest))
