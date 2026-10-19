import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.orders import update_order_status


class OrderStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = SimpleNamespace(add=MagicMock(), flush=AsyncMock())
        self.order = SimpleNamespace(id=uuid4(), status="CONFIRMED", fulfilment_status="UNFULFILLED")
        self.actor = uuid4()

    async def test_completion_updates_both_status_fields_and_records_event(self):
        for status in ("FULFILLED", "DELIVERED"):
            with self.subTest(status=status):
                self.setUp()
                await update_order_status(self.db, self.order, status.lower(), "Ready", self.actor)
                self.assertEqual(self.order.status, status)
                self.assertEqual(self.order.fulfilment_status, "FULFILLED")
                event = self.db.add.call_args.args[0]
                self.assertEqual(event.from_status, "CONFIRMED")
                self.assertEqual(event.to_status, status)
                self.assertTrue(event.is_customer_visible)
                self.db.flush.assert_awaited_once()

    async def test_resaving_completed_order_repairs_legacy_fulfilment_without_duplicate_event(self):
        self.order.status = "FULFILLED"
        await update_order_status(self.db, self.order, "FULFILLED", None, self.actor)
        self.assertEqual(self.order.fulfilment_status, "FULFILLED")
        self.db.add.assert_not_called()

    async def test_processing_does_not_claim_fulfilment(self):
        await update_order_status(self.db, self.order, "PROCESSING", None, self.actor)
        self.assertEqual(self.order.status, "PROCESSING")
        self.assertEqual(self.order.fulfilment_status, "UNFULFILLED")

    async def test_invalid_status_does_not_mutate_order(self):
        with self.assertRaises(ValueError):
            await update_order_status(self.db, self.order, "INVALID", None, self.actor)
        self.assertEqual(self.order.status, "CONFIRMED")
        self.assertEqual(self.order.fulfilment_status, "UNFULFILLED")
        self.db.add.assert_not_called()

    async def test_cancellation_still_releases_stock_without_claiming_fulfilment(self):
        with patch("app.services.orders.release_order_reservations", new_callable=AsyncMock) as release:
            await update_order_status(self.db, self.order, "CANCELLED", None, self.actor)
        self.assertEqual(self.order.status, "CANCELLED")
        self.assertEqual(self.order.fulfilment_status, "UNFULFILLED")
        release.assert_awaited_once_with(self.db, self.order.id, note="Order cancelled by admin")
