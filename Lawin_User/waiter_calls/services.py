from django.db import IntegrityError, transaction
from django.utils import timezone

from core import events
from core.errors import Conflict, NotFound
from tables.models import Table

from .models import WaiterCall
from .serializers import call_dict

ACTIVE = [WaiterCall.Status.PENDING, WaiterCall.Status.ACKNOWLEDGED]


def _publish(event, call):
    data = {"call": call_dict(call)}
    events.publish_admin(event, data)
    events.publish_table(call.table_id, event, data)  # مشتریان همان میز


@transaction.atomic
def request_call(table_id):
    """(call, created) — اگر درخواست فعال وجود داشته باشد، تکراری ساخته نمی‌شود."""
    table = Table.objects.select_for_update().get(pk=table_id)
    existing = WaiterCall.objects.select_related("table").filter(table=table, status__in=ACTIVE).first()
    if existing:
        return existing, False
    session = table.sessions.filter(exited_at__isnull=True).first()
    try:
        with transaction.atomic():
            call = WaiterCall.objects.create(table=table, session=session)
    except IntegrityError:  # رقابت همزمان: قید یکتایی دیتابیس برنده است
        return WaiterCall.objects.select_related("table").get(table=table, status__in=ACTIVE), False
    _publish(events.WAITER_CALL_CREATED, call)
    return call, True


def _lock(call_id):
    try:
        return WaiterCall.objects.select_for_update().select_related("table").get(pk=call_id)
    except WaiterCall.DoesNotExist:
        raise NotFound("درخواست پیدا نشد.")


@transaction.atomic
def acknowledge(call_id, user=None):
    call = _lock(call_id)
    if call.status == WaiterCall.Status.ACKNOWLEDGED:
        return call
    if call.status != WaiterCall.Status.PENDING:
        raise Conflict("این درخواست قبلاً انجام شده است.")
    call.status, call.acknowledged_at = WaiterCall.Status.ACKNOWLEDGED, timezone.now()
    call.acknowledged_by = user if getattr(user, "pk", None) else None
    call.save()
    _publish(events.WAITER_CALL_ACKNOWLEDGED, call)
    return call


@transaction.atomic
def complete(call_id):
    call = _lock(call_id)
    if call.status == WaiterCall.Status.COMPLETED:
        return call
    call.status, call.completed_at = WaiterCall.Status.COMPLETED, timezone.now()
    call.save()
    _publish(events.WAITER_CALL_COMPLETED, call)
    return call


def complete_active_for_table(table):
    for call in WaiterCall.objects.select_for_update().select_related("table").filter(table=table, status__in=ACTIVE):
        call.status, call.completed_at = WaiterCall.Status.COMPLETED, timezone.now()
        call.save()
        _publish(events.WAITER_CALL_COMPLETED, call)
