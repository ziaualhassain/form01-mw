from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Address, Cart, CartItem, CouponRedemption, Order, OrderAddress, OrderItem, Payment, Product, ProductVariant
from app.services.orders import create_payment_for_order
from app.services.payments import create_razorpay_order
from app.services.inventory import get_available_quantities, reserve_inventory
from app.services.coupons import validate_coupon
from app.services.invoices import snapshot_draft_invoice
from app.services.gst import checkout_test_gst, current_gst_draft
from app.core.config import get_settings


async def quote_cart(db: AsyncSession, cart_id, user_id=None, coupon_code: str | None = None, shipping_address_id=None) -> dict:
    result = await db.execute(
        select(CartItem, ProductVariant, Product)
        .join(ProductVariant, CartItem.variant_id == ProductVariant.id)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(CartItem.cart_id == cart_id).order_by(CartItem.id)
    )
    subtotal = 0
    items = []
    rows = result.all()
    availability = await get_available_quantities(db, [cart_item.variant_id for cart_item, _, _ in rows])
    for cart_item, variant, _ in rows:
        line_total = variant.price_minor * cart_item.quantity
        subtotal += line_total
        items.append({"id": cart_item.id, "variant_id": cart_item.variant_id, "quantity": cart_item.quantity, "available_quantity": availability.get(cart_item.variant_id, {}).get("available", 0)})
    coupon, discount = await validate_coupon(db, coupon_code, user_id, subtotal) if coupon_code else (None, 0)
    gst_test = None
    if get_settings().gst_checkout_test_mode:
        address = (await db.execute(select(Address).where(Address.id == shipping_address_id, Address.user_id == user_id))).scalar_one_or_none()
        if address is None:
            raise ValueError("Choose an owned shipping address before testing GST")
        gst_test = await checkout_test_gst(db, rows, address, discount)
    tax = gst_test["tax_minor"] if gst_test else 0
    return {"currency": "INR", "subtotal_minor": subtotal, "discount_minor": discount, "shipping_minor": 0, "tax_minor": tax, "total_minor": subtotal - discount + tax, "items": items, "coupon_code": coupon.code if coupon else None, "gst_test": gst_test}


async def place_order(db: AsyncSession, cart_id, contact_email: str, user_id=None, shipping_address_id=None, billing_address_id=None, payment_provider: str = "mock", idempotency_key: str | None = None, coupon_code: str | None = None, gst_test_version: int | None = None):
    settings = get_settings()
    test_mode = settings.gst_checkout_test_mode
    if test_mode and payment_provider != "razorpay":
        raise ValueError("GST test checkout requires Razorpay Test payments, not mock payments")
    if test_mode and gst_test_version is None:
        raise ValueError("Refresh the cart to obtain a GST test quote before checkout")
    if not test_mode and gst_test_version is not None:
        raise ValueError("GST test mode is disabled; refresh the cart before checkout")
    cart = (await db.execute(select(Cart).where(Cart.id == cart_id, Cart.user_id == user_id).with_for_update())).scalar_one_or_none()
    if cart is None:
        raise ValueError("Cart was not found for this account")
    if idempotency_key:
        existing_payment = await db.execute(select(Payment).where(Payment.idempotency_key == idempotency_key))
        payment = existing_payment.scalar_one_or_none()
        if payment is not None:
            existing_order = await db.execute(select(Order).where(Order.id == payment.order_id))
            order = existing_order.scalar_one()
            if order.user_id != user_id:
                raise ValueError("Checkout key belongs to another account")
            if payment.status != "PENDING":
                raise ValueError("This checkout has finished; refresh your cart before trying again")
            if order.coupon_code != (coupon_code.strip().upper() if coupon_code else None):
                raise ValueError("Checkout key was already used with another coupon")
            saved_test = (order.draft_invoice_snapshot or {}).get("gst_test")
            if bool(saved_test) != test_mode or (
                saved_test and saved_test["configuration_version"] != gst_test_version
            ):
                raise ValueError("Checkout key was already used with different GST test settings; start a new checkout")
            return order, payment

    cart_result = await db.execute(
        select(CartItem, ProductVariant, Product)
        .join(ProductVariant, CartItem.variant_id == ProductVariant.id)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(CartItem.cart_id == cart_id).order_by(CartItem.id)
    )
    rows = cart_result.all()
    if not rows:
        raise ValueError("Cart is empty")
    if user_id is None or shipping_address_id is None:
        raise ValueError("A shipping address is required before checkout")

    shipping_result = await db.execute(select(Address).where(Address.id == shipping_address_id, Address.user_id == user_id))
    shipping_address = shipping_result.scalar_one_or_none()
    if shipping_address is None:
        raise ValueError("Shipping address was not found for this account")
    billing_address = shipping_address
    if billing_address_id is not None:
        billing_result = await db.execute(select(Address).where(Address.id == billing_address_id, Address.user_id == user_id))
        billing_address = billing_result.scalar_one_or_none()
        if billing_address is None:
            raise ValueError("Billing address was not found for this account")

    subtotal = 0
    order = Order(order_number=f"{'TEST-GST-' if test_mode else ''}FO-{uuid4().hex[:12].upper()}", user_id=user_id, contact_email=contact_email, placed_at=datetime.now(timezone.utc))
    db.add(order)
    await db.flush()

    provider_order_id = None

    for cart_item, variant, product in rows:
        if product.status != "ACTIVE" or variant.status != "ACTIVE":
            raise ValueError("A cart product is no longer available; remove it before checkout")
        line_total = variant.price_minor * cart_item.quantity
        subtotal += line_total
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=variant.product_id,
                variant_id=variant.id,
                product_name_snapshot=product.title,
                variant_label_snapshot=variant.title,
                sku_snapshot=variant.sku,
                product_slug_snapshot=product.slug,
                image_url_snapshot=None,
                unit_price_minor=variant.price_minor,
                quantity=cart_item.quantity,
                line_total_minor=line_total,
            )
        )

    order.subtotal_minor = subtotal
    coupon, discount = await validate_coupon(db, coupon_code, user_id, subtotal, reserve=True) if coupon_code else (None, 0)
    configuration = await current_gst_draft(db)
    gst_test = None
    if test_mode:
        if configuration[0] != gst_test_version:
            raise ValueError("Draft GST settings changed; refresh the cart and review a new quote")
        gst_test = await checkout_test_gst(db, rows, shipping_address, discount, configuration)
    order.discount_minor = discount
    order.tax_minor = gst_test["tax_minor"] if gst_test else 0
    order.total_minor = subtotal - discount + order.tax_minor
    order.coupon_code = coupon.code if coupon else None
    order.draft_invoice_snapshot = await snapshot_draft_invoice(db, order, rows, shipping_address, configuration, gst_test)
    order.cart_id = cart_id
    order.cart_items_snapshot = [{"id": str(item.id), "quantity": item.quantity} for item, _, _ in rows]
    if coupon is not None:
        db.add(CouponRedemption(coupon_id=coupon.id, order_id=order.id, user_id=user_id, status="RESERVED"))
    for address_type, address in (("SHIPPING", shipping_address), ("BILLING", billing_address)):
        db.add(
            OrderAddress(
                order_id=order.id,
                address_type=address_type,
                recipient_name=address.recipient_name,
                phone=address.phone,
                line1=address.line1,
                line2=address.line2,
                locality=address.locality,
                city=address.city,
                state_or_region=address.state_or_region,
                postal_code=address.postal_code,
                country_code=address.country_code,
                delivery_instructions=address.delivery_instructions,
            )
        )

    await reserve_inventory(
        db,
        order.id,
        {cart_item.variant_id: cart_item.quantity for cart_item, _, _ in rows},
    )

    if payment_provider == "razorpay":
        provider_notes = {
            "customer_id": str(user_id) if user_id is not None else "guest",
            "order_number": order.order_number,
            "customer_email": contact_email,
        }
        if gst_test:
            provider_notes["gst_mode"] = "LOCAL_TEST_ONLY"
        provider_order = create_razorpay_order(
            amount_minor=order.total_minor,
            currency=order.currency,
            receipt=order.order_number,
            notes=provider_notes,
        )
        provider_order_id = provider_order.get("id")

    payment = await create_payment_for_order(
        db,
        order,
        provider=payment_provider,
        idempotency_key=idempotency_key or f"checkout-{order.order_number}",
        provider_order_id=provider_order_id,
    )
    order.status = "PENDING"
    await db.commit()
    await db.refresh(order)
    await db.refresh(payment)
    return order, payment
