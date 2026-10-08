"""انتشار رویدادهای Real-Time.

قرارداد پیام (برای مشتری و Flutter یکسان):
    {"event": "<name>", "data": {...}, "id": "<uuid>", "ts": "<ISO-8601 UTC>"}

انتشار همیشه بعد از commit تراکنش انجام می‌شود تا رویدادِ یک تراکنش rollback‌شده
هرگز ارسال نشود؛ و خطای Channel Layer هیچ‌وقت ثبت سفارش را خراب نمی‌کند.
"""
import logging
import uuid

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone

log = logging.getLogger(__name__)

ADMIN_GROUP = "admin_dashboard"

# نام رویدادها
ORDER_CREATED = "order_created"
ORDER_STATUS_CHANGED = "order_status_changed"
WAITER_CALL_CREATED = "waiter_call_created"
WAITER_CALL_ACKNOWLEDGED = "waiter_call_acknowledged"
WAITER_CALL_COMPLETED = "waiter_call_completed"
TABLE_STATUS_CHANGED = "table_status_changed"
PAYMENT_COMPLETED = "payment_completed"
# نسیه (Accounts Receivable): ایجاد طلب، وصول (مقدار یا کل)، و بسته‌شدن نسیه
CREDIT_CREATED = "credit_created"
CREDIT_PAYMENT_CREATED = "credit_payment_created"
CREDIT_SETTLED = "credit_settled"


def table_group(table_id): return f"table_{table_id}"
def customer_group(key): return f"customer_{key}"


def _send(group, event, data):
    message = {"event": event, "data": data, "id": uuid.uuid4().hex,
               "ts": timezone.now().isoformat()}
    try:
        async_to_sync(get_channel_layer().group_send)(group, {"type": "broadcast", "event": message})
    except Exception:  # noqa: BLE001
        log.exception("WebSocket publish failed: %s -> %s", event, group)


def publish(group, event, data):
    transaction.on_commit(lambda: _send(group, event, data))


def publish_admin(event, data): publish(ADMIN_GROUP, event, data)
def publish_table(table_id, event, data): publish(table_group(table_id), event, data)
def publish_customer(key, event, data):
    if key:
        publish(customer_group(key), event, data)
