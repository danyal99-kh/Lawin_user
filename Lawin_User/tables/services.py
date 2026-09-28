from django.utils import timezone

from core import events
from orders.constants import OPEN_STATUSES

from .models import Table, TableSession
from .serializers import session_dict, table_dict


def _publish_status(table, session):
    events.publish_admin(events.TABLE_STATUS_CHANGED,
                         {"table": table_dict(table), "active_session": session_dict(session, table)})


def open_session(table_id, now=None):
    """قانون ۱: با اولین سفارش نشست ساخته و میز فعال می‌شود. باید داخل transaction.atomic باشد.
    (session, table) برمی‌گرداند."""
    now = now or timezone.now()
    table = Table.objects.select_for_update().get(pk=table_id)
    session = table.sessions.filter(exited_at__isnull=True).first()
    if session is None:
        session = TableSession.objects.create(table=table, entered_at=now)
    if table.status != Table.Status.ACTIVE:
        table.status = Table.Status.ACTIVE
        table.save(update_fields=["status"])
        _publish_status(table, session)
    return session, table


def close_session(session, now=None):
    """قانون ۲: خروج ثبت، میز خالی و درخواست‌های گارسون باز بسته می‌شود."""
    now = now or timezone.now()
    session.exited_at = now
    session.save(update_fields=["exited_at"])
    table = Table.objects.select_for_update().get(pk=session.table_id)
    if table.status == Table.Status.ACTIVE:
        table.status = Table.Status.EMPTY
        table.save(update_fields=["status"])
    from waiter_calls.services import complete_active_for_table  # import محلی: جلوگیری از حلقه
    complete_active_for_table(table)
    _publish_status(table, None)


def close_session_if_idle(session, now=None):
    """اگر سفارش بازی در نشست نمانده باشد آن را می‌بندد؛ True یعنی بسته شد."""
    if session.exited_at is None and not session.orders.filter(status__in=OPEN_STATUSES).exists():
        close_session(session, now)
        return True
    return False
