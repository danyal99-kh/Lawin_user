from collections import defaultdict

from rest_framework.decorators import api_view
from rest_framework.response import Response

from core import events
from core.errors import Conflict, Invalid, NotFound
from django.db import transaction
from orders import services as order_services
from orders.constants import OPEN_STATUSES
from orders.models import Order
from orders.serializers import order_dict
from waiter_calls.models import WaiterCall
from waiter_calls.serializers import call_dict
from waiter_calls import services as call_services

from .models import Table, TableSession
from .serializers import session_dict, table_dict


def overview(t, active, last, orders, call):
    amount = sum(o.total for o in orders)
    return {
        "table": table_dict(t), "active_session": session_dict(active, t), "last_session": session_dict(last, t),
        "open_orders": [order_dict(o) for o in orders], "current_amount": amount,
        "payment_status": "unpaid" if orders else ("paid" if active is None and last else "none"),
        "waiter_call": call_dict(call) if call else None,
    }


def build_overviews(tables):
    ids = [t.id for t in tables]
    active = {s.table_id: s for s in TableSession.objects.filter(table_id__in=ids, exited_at__isnull=True)}
    orders = defaultdict(list)
    for o in (Order.objects.select_related("table").prefetch_related("items")
              .filter(table_id__in=ids, status__in=OPEN_STATUSES).order_by("created_at")):
        orders[o.table_id].append(o)
    calls = {c.table_id: c for c in WaiterCall.objects.select_related("table")
             .filter(table_id__in=ids, status__in=call_services.ACTIVE)}
    result = []
    for t in tables:
        last = None if t.id in active else t.sessions.filter(exited_at__isnull=False).order_by("-exited_at").first()
        result.append(overview(t, active.get(t.id), last, orders[t.id], calls.get(t.id)))
    return result


@api_view(["GET"])
def tables(request):
    return Response(build_overviews(list(Table.objects.all())))


@api_view(["POST"])
def reserve(request, pk):
    reserved = request.data.get("reserved")
    if not isinstance(reserved, bool):
        raise Invalid("مقدار reserved باید true یا false باشد.")
    with transaction.atomic():
        try:
            t = Table.objects.select_for_update().get(pk=pk)
        except Table.DoesNotExist:
            raise NotFound("میز پیدا نشد.")
        if t.status == Table.Status.ACTIVE:
            raise Conflict("میز فعال است و قابل رزرو نیست.")
        t.status = Table.Status.RESERVED if reserved else Table.Status.EMPTY
        t.save(update_fields=["status"])
        events.publish_admin(events.TABLE_STATUS_CHANGED, {"table": table_dict(t), "active_session": None})
    return Response(build_overviews([t])[0])


@api_view(["POST"])
def pay_table(request, pk):
    """پرداخت همه‌ی سفارش‌های باز میز → Order=paid, Session=closed, Table=empty.

    پرداخت نسیه (`method: "credit"` یا سهم credit در `payments`) نیازمند
    `debtor_name` است؛ بقیه‌ی روش‌ها بی‌اثرند.
    """
    order_services.pay_table(pk, request.data.get("method"), request.user,
                             payments=request.data.get("payments"),
                             debtor_name=request.data.get("debtor_name"))
    return Response(build_overviews([Table.objects.get(pk=pk)])[0])
