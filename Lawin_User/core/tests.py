"""تست‌های WebSocket (سناریوهای ۱، ۲، ۶). TransactionTestCase چون رویدادها بعد از commit واقعی ارسال می‌شوند."""

import json

from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import Client, TransactionTestCase

from cafebook.asgi import application
from core.testing import make_world, scan


class WebSocketTests(TransactionTestCase):
    async def _admin(self, token):
        comm = WebsocketCommunicator(application, f"/ws/admin/?token={token}")
        ok, _ = await comm.connect()
        return comm, ok

    async def test_rejects_bad_token(self):
        comm, ok = await self._admin("wrong")
        self.assertFalse(ok)

    async def test_order_and_waiter_events_reach_admin(self):
        w = await sync_to_async(make_world)()
        comm, ok = await self._admin(w.token)
        self.assertTrue(ok)
        self.assertEqual((await comm.receive_json_from())["event"], "connected")

        def customer_flow():
            c = Client()
            scan(c, w.t2)
            c.post(
                "/api/customer/orders/",
                json.dumps({"items": [{"product_id": w.cake.id, "quantity": 1}]}),
                content_type="application/json",
            )
            c.post("/api/customer/waiter/", "{}", content_type="application/json")

        await sync_to_async(customer_flow)()

        seen = [(await comm.receive_json_from())["event"] for _ in range(3)]
        self.assertEqual(
            seen, ["table_status_changed", "order_created", "waiter_call_created"]
        )
        await comm.disconnect()

    async def test_scenario6_reconnect_and_resync(self):
        w = await sync_to_async(make_world)()
        comm, _ = await self._admin(w.token)
        await comm.receive_json_from()
        await comm.disconnect()  # قطع اتصال

        def order_while_offline():
            c = Client()
            scan(c, w.t5)
            c.post(
                "/api/customer/orders/",
                json.dumps({"items": [{"product_id": w.cake.id, "quantity": 1}]}),
                content_type="application/json",
            )
            return (
                Client(headers={"Authorization": f"Token {w.token}"})
                .get("/api/v1/orders/changes/?cursor=2000-01-01T00:00:00Z")
                .json()
            )

        missed = await sync_to_async(order_while_offline)()
        self.assertEqual(
            len(missed["orders"]), 1
        )  # رویداد از دست‌رفته با REST جبران می‌شود

        comm2, ok = await self._admin(w.token)  # Reconnect
        self.assertTrue(ok)
        self.assertEqual((await comm2.receive_json_from())["event"], "connected")
        await comm2.send_json_to({"type": "ping"})
        self.assertEqual((await comm2.receive_json_from())["event"], "pong")
        await comm2.disconnect()

    async def test_customer_socket_needs_session(self):
        comm = WebsocketCommunicator(
            application, "/ws/customer/", headers=[(b"origin", b"http://testserver")]
        )
        ok, _ = await comm.connect()
        self.assertFalse(ok)
