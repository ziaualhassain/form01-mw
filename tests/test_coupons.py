import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.coupons import archive_coupon, create_coupon, list_coupons, router, update_coupon
from app.api.dependencies import Principal, get_current_principal
from app.api.v1.webhooks import router as webhook_router
from app.api.v1.payments import verify_razorpay_payment
from app.schemas import RazorpayPaymentVerifyRequest
from app.db.base import Base
from app.db.models import (
    Address, Cart, CartItem, Coupon, CouponRedemption, InventoryLevel,
    InventoryLocation, Order, Payment, Product, ProductVariant, User,
)
from app.services.cart import list_cart_items
from app.services.checkout import place_order, quote_cart
from app.services.coupons import CouponRead, CouponWrite, discount_amount, validate_coupon
from app.api.v1.cart import get_cart
from app.services.orders import expire_pending_payments, mark_payment_captured, mark_payment_failed, update_order_status


class CouponRulesTests(unittest.TestCase):
    def coupon(self, **changes):
        values = dict(code="WELCOME10", discount_type="PERCENTAGE", value=10,
                      max_discount_minor=10000, min_subtotal_minor=0, enabled=True)
        values.update(changes)
        return Coupon(**values)

    def test_ten_percent_capped_at_exactly_one_hundred_rupees(self):
        self.assertEqual(discount_amount(self.coupon(), 50000), 5000)
        self.assertEqual(discount_amount(self.coupon(), 150000), 10000)
        self.assertEqual(discount_amount(self.coupon(), 10009), 1000)

    def test_fixed_discount_and_gateway_minimum(self):
        self.assertEqual(discount_amount(self.coupon(discount_type="FIXED", value=5000), 20000), 5000)
        self.assertEqual(discount_amount(self.coupon(discount_type="FIXED", value=5000), 2000), 1900)
        with self.assertRaisesRegex(ValueError, "at least INR 1"):
            discount_amount(self.coupon(), 100)

    def test_disabled_scheduled_expired_and_minimum_are_enforced(self):
        now = datetime.now(timezone.utc)
        for changes in [
            dict(enabled=False), dict(starts_at=now + timedelta(days=1)),
            dict(ends_at=now), dict(min_subtotal_minor=10001),
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                discount_amount(self.coupon(**changes), 10000, now)

    def test_write_validation_and_normalization(self):
        self.assertEqual(CouponWrite(code=" welcome10 ", discount_type="PERCENTAGE", value=10).code, "WELCOME10")
        for changes in [
            dict(value=101), dict(value=0), dict(discount_type="OTHER"),
            dict(code="bad code"), dict(usage_limit=0), dict(per_customer_limit=-1),
            dict(max_discount_minor=0), dict(min_subtotal_minor=-1),
            dict(starts_at=datetime.now()),
            dict(starts_at=datetime(2026, 2, 1, tzinfo=timezone.utc), ends_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ]:
            payload = dict(code="TEST", discount_type="PERCENTAGE", value=10)
            payload.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                CouponWrite(**payload)

    def test_admin_routes_require_authentication(self):
        app = FastAPI()
        app.include_router(router, prefix="/admin/coupons")
        with TestClient(app) as client:
            self.assertEqual(client.get("/admin/coupons").status_code, 401)
            self.assertEqual(client.post("/admin/coupons", json={}).status_code, 401)
            self.assertEqual(client.delete(f"/admin/coupons/{uuid4()}").status_code, 401)
        app.dependency_overrides[get_current_principal] = lambda: Principal(user_id=uuid4(), email="customer@example.com", roles=["CUSTOMER"])
        with TestClient(app) as client:
            self.assertEqual(client.get("/admin/coupons").status_code, 403)
            self.assertEqual(client.post("/admin/coupons", json={}).status_code, 403)
            self.assertEqual(client.delete(f"/admin/coupons/{uuid4()}").status_code, 403)


class CouponLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.user = User(email="customer@example.com", password_hash="fixture")
        self.product = Product(title="Shirt", slug="shirt", status="ACTIVE")
        self.location = InventoryLocation(code="DEFAULT", name="Warehouse")
        self.db.add_all([self.user, self.product, self.location])
        await self.db.flush()
        self.variant = ProductVariant(product_id=self.product.id, sku="SHIRT", price_minor=100000)
        self.cart = Cart(user_id=self.user.id)
        self.address = Address(user_id=self.user.id, recipient_name="Customer", line1="Test street", city="Hyderabad", postal_code="500001", country_code="IN")
        self.coupon = Coupon(**CouponWrite(code="SAVE10", discount_type="PERCENTAGE", value=10, max_discount_minor=10000, usage_limit=1, per_customer_limit=1).model_dump())
        self.db.add_all([self.variant, self.cart, self.address, self.coupon])
        await self.db.flush()
        self.item = CartItem(cart_id=self.cart.id, variant_id=self.variant.id, quantity=2)
        self.level = InventoryLevel(location_id=self.location.id, variant_id=self.variant.id, on_hand=10, reserved=0)
        self.db.add_all([self.item, self.level])
        await self.db.commit()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    async def checkout(self, coupon_code: str | None = "save10", payment_provider: str = "mock", idempotency_key: str | None = None):
        return await place_order(
            self.db, self.cart.id, self.user.email, user_id=self.user.id,
            shipping_address_id=self.address.id, coupon_code=coupon_code,
            payment_provider=payment_provider, idempotency_key=idempotency_key or str(uuid4()),
        )

    async def redemption(self, order):
        return (await self.db.execute(select(CouponRedemption).where(CouponRedemption.order_id == order.id))).scalar_one()

    async def test_quote_checkout_provider_amount_and_snapshot_agree(self):
        quote = await quote_cart(self.db, self.cart.id, self.user.id, "save10")
        self.assertEqual((quote["subtotal_minor"], quote["discount_minor"], quote["total_minor"]), (200000, 10000, 190000))
        with patch("app.services.checkout.create_razorpay_order", return_value={"id": "order_fixture"}) as provider:
            order, payment = await self.checkout(payment_provider="razorpay", idempotency_key="same-key")
        self.assertEqual(provider.call_args.kwargs["amount_minor"], 190000)
        self.assertEqual((order.total_minor, payment.amount_minor, order.coupon_code), (190000, 190000, "SAVE10"))
        self.assertEqual((await self.redemption(order)).status, "RESERVED")
        self.assertEqual(self.item.quantity, 2)
        same_order, same_payment = await self.checkout(idempotency_key="same-key")
        self.assertEqual((same_order.id, same_payment.id), (order.id, payment.id))
        with self.assertRaisesRegex(ValueError, "usage limit"):
            await validate_coupon(self.db, "SAVE10", self.user.id, 200000, reserve=True)

    async def test_capture_removes_purchased_quantity_only_and_is_idempotent(self):
        order, payment = await self.checkout()
        self.item.quantity = 3
        other = ProductVariant(product_id=self.product.id, sku="OTHER", price_minor=1000)
        self.db.add(other)
        await self.db.flush()
        added = CartItem(cart_id=self.cart.id, variant_id=other.id, quantity=1)
        self.db.add(added)
        await self.db.commit()
        await mark_payment_captured(self.db, payment, "pay_fixture")
        await self.db.commit()
        await self.db.refresh(self.item)
        await self.db.refresh(self.level)
        self.assertEqual(self.item.quantity, 1)
        self.assertEqual(self.level.on_hand, 8)
        self.assertEqual((await self.redemption(order)).status, "REDEEMED")
        self.assertIsNotNone(order.cart_cleared_at)
        await mark_payment_captured(self.db, payment, "pay_fixture")
        await mark_payment_failed(self.db, payment)
        await self.db.commit()
        self.assertEqual(self.item.quantity, 1)
        self.assertEqual(payment.status, "CAPTURED")
        self.assertEqual(len(await list_cart_items(self.db, self.cart.id)), 2)

    async def test_capture_clears_original_line_but_preserves_removed_and_readded_line(self):
        order, payment = await self.checkout()
        await mark_payment_captured(self.db, payment)
        await self.db.commit()
        self.assertEqual(await list_cart_items(self.db, self.cart.id), [])
        replacement = CartItem(cart_id=self.cart.id, variant_id=self.variant.id, quantity=1)
        self.db.add(replacement)
        await self.db.commit()
        await mark_payment_captured(self.db, payment)
        await self.db.commit()
        self.assertEqual([item.id for item in await list_cart_items(self.db, self.cart.id)], [replacement.id])

    async def test_failure_releases_coupon_and_stock_but_keeps_cart(self):
        order, payment = await self.checkout()
        await mark_payment_failed(self.db, payment)
        await self.db.commit()
        self.assertEqual((await self.redemption(order)).status, "RELEASED")
        self.assertEqual(self.item.quantity, 2)
        self.assertEqual((await validate_coupon(self.db, "SAVE10", self.user.id, 200000))[1], 10000)
        with self.assertRaisesRegex(ValueError, "reconciliation"):
            await mark_payment_captured(self.db, payment)

    async def test_expiry_releases_coupon_and_keeps_cart(self):
        order, payment = await self.checkout()
        payment.created_at = datetime.now(timezone.utc) - timedelta(minutes=16)
        await self.db.commit()
        self.assertEqual(await expire_pending_payments(self.db), 1)
        self.assertEqual((await self.redemption(order)).status, "RELEASED")
        self.assertEqual(len(await list_cart_items(self.db, self.cart.id)), 1)
        self.assertEqual(order.status, "PAYMENT_FAILED")

    async def test_cancellation_releases_reserved_coupon(self):
        order, payment = await self.checkout()
        await update_order_status(self.db, order, "CANCELLED", None, self.user.id)
        await self.db.commit()
        self.assertEqual((await self.redemption(order)).status, "RELEASED")
        self.assertEqual(payment.status, "FAILED")
        self.assertEqual(self.item.quantity, 2)

    async def test_customer_limit_and_disabled_after_quote_revalidated(self):
        self.coupon.usage_limit = None
        await self.db.commit()
        await self.checkout()
        with self.assertRaisesRegex(ValueError, "You have reached"):
            await validate_coupon(self.db, "SAVE10", self.user.id, 200000)
        self.coupon.enabled = False
        await self.db.commit()
        with self.assertRaisesRegex(ValueError, "disabled"):
            await self.checkout()
        await self.db.rollback()

    async def test_no_coupon_preserves_existing_total(self):
        order, payment = await self.checkout(coupon_code=None)
        self.assertEqual((order.total_minor, order.discount_minor, payment.amount_minor), (200000, 0, 200000))

    async def test_admin_create_edit_list_and_duplicate_code(self):
        principal = Principal(user_id=self.user.id, email=self.user.email, roles=["ADMIN"])
        payload = CouponWrite(code="fixed50", discount_type="FIXED", value=5000)
        created = await create_coupon(payload, principal, self.db)
        self.assertEqual(created.code, "FIXED50")
        self.assertEqual(len(await list_coupons(principal, self.db)), 2)
        updated = await update_coupon(created.id, payload.model_copy(update={"enabled": False}), principal, self.db)
        self.assertFalse(updated.enabled)
        with self.assertRaises(HTTPException) as error:
            await create_coupon(payload, principal, self.db)
        self.assertEqual(error.exception.status_code, 409)

    async def test_new_checkout_revalidates_coupon_after_quote_and_rolls_back(self):
        await quote_cart(self.db, self.cart.id, self.user.id, "SAVE10")
        self.coupon.enabled = False
        await self.db.commit()
        with self.assertRaisesRegex(ValueError, "disabled"):
            await self.checkout()
        await self.db.rollback()
        self.assertEqual((await self.db.execute(select(Order))).scalars().all(), [])
        with self.assertRaisesRegex(ValueError, "invalid"):
            await validate_coupon(self.db, "UNKNOWN", uuid4(), 200000)

    async def test_provider_failure_rolls_back_coupon_stock_and_order(self):
        with patch("app.services.checkout.create_razorpay_order", side_effect=ValueError("Provider unavailable")):
            with self.assertRaisesRegex(ValueError, "Provider unavailable"):
                await self.checkout(payment_provider="razorpay")
        await self.db.rollback()
        self.assertEqual((await self.db.execute(select(CouponRedemption))).scalars().all(), [])
        self.assertEqual((await self.db.execute(select(Order))).scalars().all(), [])
        level = (await self.db.execute(select(InventoryLevel))).scalar_one()
        self.assertEqual(level.reserved, 0)
        self.assertEqual((await self.db.execute(select(CartItem))).scalar_one().quantity, 2)

    async def test_removed_and_readded_line_before_capture_is_preserved(self):
        order, payment = await self.checkout()
        cart_id, variant_id = self.cart.id, self.variant.id
        await self.db.delete(self.item)
        await self.db.flush()
        replacement = CartItem(cart_id=cart_id, variant_id=variant_id, quantity=1)
        self.db.add(replacement)
        await self.db.commit()
        await mark_payment_captured(self.db, payment)
        await self.db.commit()
        self.assertEqual([item.id for item in await list_cart_items(self.db, cart_id)], [replacement.id])

    async def test_webhook_before_browser_verification_clears_cart_and_duplicate_is_safe(self):
        order, payment = await self.checkout()
        payment.provider = "razorpay"
        payment.provider_order_id = "order_webhook"
        await self.db.commit()
        app = FastAPI()
        app.include_router(webhook_router, prefix="/webhooks")
        payload = {"event": "payment.captured", "payload": {"payment": {"entity": {
            "id": "pay_webhook", "order_id": "order_webhook", "amount": 190000, "currency": "INR",
        }}}}
        with patch("app.api.v1.webhooks.async_session_factory", async_sessionmaker(self.engine, expire_on_commit=False)), patch("app.api.v1.webhooks.verify_razorpay_webhook_signature", return_value=True):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fixture") as client:
                response = await client.post("/webhooks/razorpay", json=payload, headers={"X-Razorpay-Signature": "fixture"})
                self.assertEqual(response.status_code, 200, response.text)
                duplicate = await client.post("/webhooks/razorpay", json=payload, headers={"X-Razorpay-Signature": "fixture"})
                self.assertTrue(duplicate.json()["data"]["duplicate"])
        await self.db.refresh(order)
        await self.db.refresh(self.level)
        self.assertEqual((order.status, order.payment_status), ("CONFIRMED", "PAID"))
        self.assertEqual(self.level.on_hand, 8)
        self.assertEqual(await list_cart_items(self.db, self.cart.id), [])
        self.assertEqual((await self.redemption(order)).status, "REDEEMED")

    async def test_browser_verification_uses_same_idempotent_cart_completion(self):
        order, payment = await self.checkout()
        payment.provider = "razorpay"
        payment.provider_order_id = "order_browser"
        await self.db.commit()
        request = RazorpayPaymentVerifyRequest(razorpay_order_id="order_browser", razorpay_payment_id="pay_browser", razorpay_signature="fixture")
        principal = Principal(user_id=self.user.id, email=self.user.email, roles=["CUSTOMER"])
        provider = {"order_id": "order_browser", "amount": 190000, "currency": "INR", "status": "captured"}
        with patch("app.api.v1.payments.verify_razorpay_payment_signature", return_value=True), patch("app.api.v1.payments.fetch_razorpay_payment", return_value=provider):
            response = await verify_razorpay_payment(request, principal, self.db)
            duplicate = await verify_razorpay_payment(request, principal, self.db)
        self.assertTrue(response["data"]["verified"])
        self.assertTrue(duplicate["data"]["verified"])
        self.assertEqual(await list_cart_items(self.db, self.cart.id), [])
        self.assertEqual((await self.redemption(order)).status, "REDEEMED")

    async def test_cart_read_does_not_remove_stock_held_by_pending_payment(self):
        self.level.on_hand = 2
        await self.db.commit()
        await self.checkout()
        principal = Principal(user_id=self.user.id, email=self.user.email, roles=["CUSTOMER"])
        cart = await get_cart(principal, self.db)
        self.assertEqual((cart.items[0].quantity, cart.items[0].available_quantity), (2, 0))
        self.assertEqual(len(await list_cart_items(self.db, self.cart.id)), 1)

    async def test_admin_dates_round_trip_in_utc(self):
        principal = Principal(user_id=self.user.id, email=self.user.email, roles=["ADMIN"])
        payload = CouponWrite(code="DATED", discount_type="FIXED", value=100, starts_at=datetime.fromisoformat("2027-01-01T10:00:00+05:30"))
        coupon = await create_coupon(payload, principal, self.db)
        read = CouponRead.model_validate(coupon)
        self.assertIsNotNone(read.starts_at)
        assert read.starts_at is not None
        self.assertEqual(read.starts_at.isoformat(), "2027-01-01T04:30:00+00:00")

    async def test_archive_hides_coupon_blocks_new_use_and_preserves_pending_order(self):
        order, payment = await self.checkout()
        principal = Principal(user_id=self.user.id, email=self.user.email, roles=["ADMIN"])
        coupon_id = self.coupon.id
        await archive_coupon(coupon_id, principal, self.db)
        self.assertEqual(await list_coupons(principal, self.db), [])
        self.assertIsNotNone(await self.db.get(Coupon, coupon_id))
        with self.assertRaisesRegex(ValueError, "archived"):
            await validate_coupon(self.db, "SAVE10", self.user.id, 200000)
        with self.assertRaises(HTTPException) as error:
            await update_coupon(coupon_id, CouponWrite(code="SAVE10", discount_type="FIXED", value=100), principal, self.db)
        self.assertEqual(error.exception.status_code, 404)
        await archive_coupon(coupon_id, principal, self.db)
        await mark_payment_captured(self.db, payment)
        await self.db.commit()
        self.assertEqual((order.discount_minor, order.total_minor), (10000, 190000))
        self.assertEqual((await self.redemption(order)).status, "REDEEMED")
        with self.assertRaises(HTTPException) as duplicate:
            await create_coupon(CouponWrite(code="SAVE10", discount_type="FIXED", value=100), principal, self.db)
        self.assertEqual(duplicate.exception.status_code, 409)

    async def test_archive_unknown_coupon_returns_404(self):
        principal = Principal(user_id=self.user.id, email=self.user.email, roles=["ADMIN"])
        with self.assertRaises(HTTPException) as error:
            await archive_coupon(uuid4(), principal, self.db)
        self.assertEqual(error.exception.status_code, 404)
