from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Address, CartItem, Order, OrderAddress, OrderItem, Payment, Product, ProductVariant
from app.services.orders import create_payment_for_order
from app.services.payments import create_razorpay_order
from app.services.inventory import get_available_quantities, reserve_inventory
from app.services.cart import reconcile_cart_inventory


async def quote_cart(db: AsyncSession, cart_id) -> dict:
    await reconcile_cart_inventory(db, cart_id)
    result = await db.execute(
        select(CartItem, ProductVariant).join(ProductVariant, CartItem.variant_id == ProductVariant.id).where(CartItem.cart_id == cart_id)
    )
    subtotal = 0
    items = []
    rows = result.all()
    availability = await get_available_quantities(db, [cart_item.variant_id for cart_item, _ in rows])
    for cart_item, variant in rows:
        line_total = variant.price_minor * cart_item.quantity
        subtotal += line_total
        items.append({"id": cart_item.id, "variant_id": cart_item.variant_id, "quantity": cart_item.quantity, "available_quantity": availability.get(cart_item.variant_id, {}).get("available", 0)})
    return {"currency": "INR", "subtotal_minor": subtotal, "discount_minor": 0, "shipping_minor": 0, "tax_minor": 0, "total_minor": subtotal, "items": items}


async def place_order(db: AsyncSession, cart_id, contact_email: str, user_id=None, shipping_address_id=None, billing_address_id=None, payment_provider: str = "mock", idempotency_key: str | None = None):
    if idempotency_key:
        existing_payment = await db.execute(select(Payment).where(Payment.idempotency_key == idempotency_key))
        payment = existing_payment.scalar_one_or_none()
        if payment is not None:
            existing_order = await db.execute(select(Order).where(Order.id == payment.order_id))
            order = existing_order.scalar_one()
            return order, payment

    await reconcile_cart_inventory(db, cart_id, commit=False)
    cart_result = await db.execute(
        select(CartItem, ProductVariant, Product)
        .join(ProductVariant, CartItem.variant_id == ProductVariant.id)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(CartItem.cart_id == cart_id)
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
    order = Order(order_number=f"FO-{uuid4().hex[:12].upper()}", user_id=user_id, contact_email=contact_email, placed_at=datetime.now(timezone.utc))
    db.add(order)
    await db.flush()

    provider_order_id = None

    for cart_item, variant, product in rows:
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
    order.total_minor = subtotal
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
        provider_order = create_razorpay_order(
            amount_minor=subtotal,
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
