from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone
from rest_framework.authtoken.models import Token

from .events import ADMIN_GROUP, customer_group, table_group


class BaseConsumer(AsyncJsonWebsocketConsumer):
    groups_joined: list

    async def join(self, *groups):
        self.groups_joined = list(groups)
        for g in groups:
            await self.channel_layer.group_add(g, self.channel_name)

    async def disconnect(self, code):
        for g in getattr(self, "groups_joined", []):
            await self.channel_layer.group_discard(g, self.channel_name)

    async def broadcast(self, message):  # handler برای group_send(type="broadcast")
        await self.send_json(message["event"])

    async def receive_json(self, content, **kwargs):
        if content.get("type") == "ping":
            await self.send_json({"event": "pong", "data": {"ts": timezone.now().isoformat()}})

    async def hello(self):
        await self.send_json({"event": "connected", "data": {"server_time": timezone.now().isoformat()}})


class AdminConsumer(BaseConsumer):
    """پنل Flutter: ws://host/ws/admin/?token=<Token> (یا هدر Authorization: Token <key>)."""

    async def connect(self):
        qs = parse_qs(self.scope.get("query_string", b"").decode())
        token = (qs.get("token") or [None])[0]
        if not token:
            headers = dict(self.scope.get("headers", []))
            auth = headers.get(b"authorization", b"").decode()
            if auth.lower().startswith("token "):
                token = auth[6:].strip()
        if not token or not await self._is_staff(token):
            await self.close(code=4401)
            return
        await self.join(ADMIN_GROUP)
        await self.accept()
        await self.hello()

    @database_sync_to_async
    def _is_staff(self, key):
        try:
            u = Token.objects.select_related("user").get(key=key).user
        except Token.DoesNotExist:
            return False
        return u.is_active and u.is_staff


class CustomerConsumer(BaseConsumer):
    """مشتری: فقط با نشست معتبر QR. رویداد سفارش‌ها فقط به همان مرورگر می‌رسد."""

    async def connect(self):
        session = self.scope.get("session")
        if session is None:
            await self.close(code=4403)
            return
        key, table_id = await database_sync_to_async(
            lambda: (session.get("customer_key"), session.get("table_id")))()
        if not key or not table_id:
            await self.close(code=4403)
            return
        await self.join(customer_group(key), table_group(table_id))
        await self.accept()
        await self.hello()
