from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.errors import Invalid, NotFound

from . import services
from .constants import OrderSource
from .models import Order
from .serializers import order_dict

QS = Order.objects.select_related("table").prefetch_related("items", "payments")


def _get(pk):
    try:
        return QS.get(pk=pk)
    except Order.DoesNotExist:
        raise NotFound("سفارش پیدا نشد.")


@api_view(["GET", "POST"])
def orders(request):
    if request.method == "POST":
        d = request.data
        o = services.create_order(table_id=d.get("table_id"), items=d.get("items"),
                                  source=OrderSource.ADMIN, customer_note=d.get("customer_note") or "",
                                  idempotency_key=d.get("idempotency_key"))
        return Response(order_dict(_get(o.pk)), status=201)
    qs = QS.all()
    if s := request.query_params.get("status"):
        qs = qs.filter(status=s)
    return Response([order_dict(o) for o in qs[:500]])


@api_view(["GET"])
def orders_changes(request):
    """دریافت افزایشی/همگام‌سازی بعد از قطع WebSocket: ?cursor=<ISO time>. cursor بعدی در پاسخ است."""
    raw = request.query_params.get("cursor")
    since = parse_datetime(raw) if raw else timezone.now() - timedelta(days=1)
    if since is None:
        raise Invalid("cursor نامعتبر است.")
    rows = list(QS.filter(updated_at__gt=since).order_by("updated_at")[:500])
    cursor = rows[-1].updated_at.isoformat() if rows else (raw or since.isoformat())
    return Response({"orders": [order_dict(o) for o in rows], "cursor": cursor})


@api_view(["GET"])
def order_detail(request, pk):
    return Response(order_dict(_get(pk)))


@api_view(["POST"])
def order_status(request, pk):
    services.change_status(pk, request.data.get("status"))
    return Response(order_dict(_get(pk)))


@api_view(["POST"])
def order_pay(request, pk):
    """پرداخت یک سفارش. بدنه: {"method": "cash"} یا پرداخت چندروشی
    {"payments": [{"method": "cash", "amount": 500000}, ...]}.
    برای نسیه `debtor_name` هم لازم است."""
    services.pay_order(pk, request.data.get("method"), request.user,
                       payments=request.data.get("payments"),
                       debtor_name=request.data.get("debtor_name"))
    return Response(order_dict(_get(pk)))


@api_view(["POST"])
def order_refund(request, pk):
    """برگشت کامل سفارش پرداخت‌شده: درآمد، پول، موجودی و COGS خنثا می‌شود."""
    services.refund_order(pk, request.user)
    return Response(order_dict(_get(pk)))


@api_view(["POST"])
def order_bar_printed(request, pk):
    services.mark_bar_printed(pk)
    return Response(order_dict(_get(pk)))
