import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.dependencies import Principal, get_current_principal
from app.api.v1.admin_order_queue import router
from app.db.base import Base
from app.db.models import Order, OrderItem
from app.db.session import get_db


class OrderQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = datetime(2026, 10, 20, 10, tzinfo=timezone.utc)
        owner = self

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return owner.now.astimezone(tz)

        self.clock = patch("app.api.v1.admin_order_queue.datetime", Clock)
        self.clock.start()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.orders = []
        for i in range(55):
            order = self.order(f"TODAY-{i:03d}", self.now - timedelta(minutes=i + 1))
            self.db.add(order)
            self.orders.append(order)
        boundary = datetime(2026, 10, 19, 18, 30, tzinfo=timezone.utc)
        self.db.add_all([
            self.order("DAY-START", boundary),
            self.order("YESTERDAY-END", boundary - timedelta(microseconds=1)),
            self.order("SEVEN-DAY-START", boundary - timedelta(days=6)),
            self.order("BEFORE-SEVEN", boundary - timedelta(days=6, microseconds=1)),
            self.order("THIRTY-DAY-START", boundary - timedelta(days=29)),
            self.order("BEFORE-THIRTY", boundary - timedelta(days=29, microseconds=1)),
        ])
        await self.db.flush()
        for order, name, quantity in [
            (self.orders[0], r"Literal_100%\Path", 2),
            (self.orders[0], r"Literal_100%\Path", 3),
            (self.orders[54], "Unique older product", 1),
        ]:
            self.db.add(OrderItem(
                order_id=order.id, product_id=None, variant_id=None, sku_snapshot="FIXTURE",
                product_name_snapshot=name, product_slug_snapshot="fixture", unit_price_minor=100, quantity=quantity, line_total_minor=quantity * 100,
            ))
        self.orders[54].status = "PROCESSING"
        self.orders[54].payment_status = "PAID"
        self.orders[54].fulfilment_status = "PARTIALLY_FULFILLED"
        await self.db.commit()
        self.app = FastAPI()
        self.app.include_router(router, prefix="/admin/order-queue")
        async def session():
            yield self.db
        self.app.dependency_overrides[get_db] = session
        self.principal = Principal(user_id=uuid4(), email="admin@example.test", roles=["ADMIN"])
        self.app.dependency_overrides[get_current_principal] = lambda: self.principal
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    def order(self, number, created):
        return Order(
            order_number=number, created_at=created, placed_at=created, contact_email="private@example.test",
            status="CONFIRMED", payment_status="UNPAID", fulfilment_status="UNFULFILLED",
            total_minor=9900, currency="INR",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.db.close()
        await self.engine.dispose()
        self.clock.stop()

    async def get(self, **params):
        response = await self.client.get("/admin/order-queue", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_today_defaults_and_pages_beyond_fifty(self):
        first = await self.get()
        self.assertEqual((first["total"], first["limit"], first["offset"], len(first["items"])), (56, 20, 0, 20))
        self.assertEqual((first["range_start"], first["range_end"]), ("2026-10-20", "2026-10-20"))
        pages = [first] + [await self.get(offset=offset, snapshot_at=first["snapshot_at"]) for offset in [20, 40]]
        numbers = [item["order_number"] for page in pages for item in page["items"]]
        self.assertEqual(numbers, [f"TODAY-{i:03d}" for i in range(55)] + ["DAY-START"])
        self.assertEqual(len(numbers), len(set(numbers)))
        self.assertEqual(first["items"][0]["item_quantity"], 5)
        self.assertEqual(first["items"][0]["first_product"], r"Literal_100%\Path")
        self.assertNotIn("contact_email", first["items"][0])

    async def test_india_time_boundaries_and_inclusive_custom_end(self):
        for period, expected in [("7d", 58), ("30d", 60), ("all", 61)]:
            with self.subTest(period=period):
                self.assertEqual((await self.get(period=period))["total"], expected)
        custom = await self.get(period="custom", date_from="2026-10-19", date_to="2026-10-19")
        self.assertEqual([item["order_number"] for item in custom["items"]], ["YESTERDAY-END"])
        today = await self.get(period="custom", date_from="2026-10-20", date_to="2026-10-20")
        self.assertEqual(today["total"], 56)
        self.assertEqual((await self.get(period="custom", date_from="2025-01-01", date_to="2025-01-01"))["total"], 0)

    async def test_database_search_and_combined_filters_reach_old_orders(self):
        data = await self.get(q="unique OLDER", stage="PROCESSING", payment="PAID", fulfilment="PARTIALLY_FULFILLED")
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["order_number"], "TODAY-054")
        self.assertEqual((await self.get(stage="incoming"))["total"], 55)
        self.assertEqual((await self.get(q="TODAY-054"))["total"], 1)
        self.assertEqual((await self.get(q="   " ))["total"], 56)
        for q in ["_", "%", r"\Path", r"Literal_100%\Path"]:
            self.assertEqual((await self.get(q=q))["total"], 1)
        self.assertEqual((await self.get(q="' OR 1=1 --"))["total"], 0)
        self.assertEqual((await self.get(payment="REFUNDED"))["items"], [])

    async def test_snapshot_does_not_shift_pages_when_new_orders_arrive(self):
        first = await self.get()
        self.now += timedelta(seconds=10)
        self.db.add(self.order("NEW-ARRIVAL", self.now - timedelta(seconds=1)))
        await self.db.commit()
        poll = await self.get(snapshot_at=first["snapshot_at"])
        self.assertEqual(poll["items"], first["items"])
        self.assertEqual((poll["total"], poll["new_orders"]), (56, 1))
        second = await self.get(offset=20, snapshot_at=first["snapshot_at"])
        self.assertEqual(second["items"][0]["order_number"], "TODAY-020")
        fresh = await self.get()
        self.assertEqual((fresh["total"], fresh["new_orders"]), (57, 0))
        self.assertEqual(fresh["items"][0]["order_number"], "NEW-ARRIVAL")

    async def test_poll_exposes_changed_status_without_creating_new_orders(self):
        first = await self.get()
        self.orders[0].status = "PACKED"
        self.orders[0].payment_status = "PAID"
        await self.db.commit()
        poll = await self.get(snapshot_at=first["snapshot_at"])
        self.assertEqual(poll["items"][0]["status"], "PACKED")
        self.assertEqual(poll["items"][0]["payment_status"], "PAID")
        self.assertEqual(poll["new_orders"], 0)

    async def test_relative_period_stays_anchored_until_refresh_across_midnight(self):
        first = await self.get()
        self.now = datetime(2026, 10, 20, 19, tzinfo=timezone.utc)
        poll = await self.get(snapshot_at=first["snapshot_at"])
        self.assertEqual((poll["server_today"], poll["range_end"]), ("2026-10-21", "2026-10-20"))
        refreshed = await self.get()
        self.assertEqual((refreshed["server_today"], refreshed["range_end"], refreshed["total"]), ("2026-10-21", "2026-10-21", 0))

    async def test_null_placed_date_falls_back_to_created_and_ties_are_deterministic(self):
        self.orders[0].placed_at = None
        self.orders[1].placed_at = self.orders[0].created_at
        self.orders[1].created_at = self.orders[0].created_at
        await self.db.commit()
        first = await self.get(limit=1)
        second = await self.get(limit=1, offset=1, snapshot_at=first["snapshot_at"])
        expected = sorted([str(order.id) for order in self.orders[:2]], reverse=True)
        self.assertEqual([first["items"][0]["id"], second["items"][0]["id"]], expected)
        self.assertEqual(first["total"], 56)

    async def test_validation_and_pagination_bounds(self):
        for params in [
            {"period": "invalid"}, {"period": "custom"}, {"period": "custom", "date_from": "2026-10-20", "date_to": "2026-10-19"},
            {"date_from": "2026-10-20"}, {"period": "custom", "date_from": "0001-01-01", "date_to": "9999-12-31"},
            {"snapshot_at": "2026-10-20T11:00:00Z"}, {"snapshot_at": "2026-10-20T09:00:00"}, {"snapshot_at": "0001-01-01T00:00:00Z"},
            {"stage": "INVALID"}, {"payment": "INVALID"}, {"fulfilment": "INVALID"},
            {"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": 1_000_001}, {"q": "x" * 151},
        ]:
            with self.subTest(params=params):
                response = await self.client.get("/admin/order-queue", params=params)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual((await self.get(offset=100))["items"], [])

    async def test_auth_roles_and_private_response(self):
        response = await self.client.get("/admin/order-queue")
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.principal = Principal(user_id=uuid4(), email="owner@example.test", roles=["OWNER"])
        self.assertEqual((await self.client.get("/admin/order-queue")).status_code, 200)
        self.principal = Principal(user_id=uuid4(), email="customer@example.test", roles=["CUSTOMER"])
        self.assertEqual((await self.client.get("/admin/order-queue")).status_code, 403)
        del self.app.dependency_overrides[get_current_principal]
        self.assertEqual((await self.client.get("/admin/order-queue")).status_code, 401)
