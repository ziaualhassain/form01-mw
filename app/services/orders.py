from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderAddress, OrderItem, OrderStatusEvent, Payment, Refund, Shipment, ShipmentItem
from app.services.inventory import consume_order_reservations, release_order_reservations

ORDER_STATUSES = {"RECEIVED", "PROCESSING", "CONFIRMED", "PACKED", "SHIPPED", "DELIVERED", "FULFILLED", "CANCELLED", "PAYMENT_FAILED"}


async def _order_items(db: AsyncSession, order_id):
    result = await db.execute(select(OrderItem).where(OrderItem.order_id == order_id))
    items = []
    for item in result.scalars().all():
        items.append(
            {
                "id": item.id,
                "product_name_snapshot": item.product_name_snapshot,
                "variant_label_snapshot": item.variant_label_snapshot,
                "sku_snapshot": item.sku_snapshot,
                "product_slug_snapshot": item.product_slug_snapshot,
                "unit_price_minor": item.unit_price_minor,
                "quantity": item.quantity,
                "line_total_minor": item.line_total_minor,
            }
        )
    return items


async def _payments(db: AsyncSession, order_id):
    result = await db.execute(select(Payment).where(Payment.order_id == order_id).order_by(Payment.created_at.desc()))
    payments = []
    for payment in result.scalars().all():
        payments.append(
            {
                "id": payment.id,
                "provider": payment.provider,
                "provider_order_id": payment.provider_order_id,
                "provider_payment_id": payment.provider_payment_id,
                "status": payment.status,
                "amount_minor": payment.amount_minor,
                "currency": payment.currency,
            }
        )
    return payments


async def _shipments(db: AsyncSession, order_id):
    result = await db.execute(select(Shipment).where(Shipment.order_id == order_id).order_by(Shipment.created_at.desc()))
    shipments = []
    for shipment in result.scalars().all():
        shipments.append(
            {
                "id": shipment.id,
                "carrier": shipment.carrier,
                "tracking_number": shipment.tracking_number,
                "tracking_url": shipment.tracking_url,
                "status": shipment.status,
                "shipped_at": shipment.shipped_at,
                "delivered_at": shipment.delivered_at,
            }
        )
    return shipments


async def get_customer_orders(db: AsyncSession, user_id) -> list[Order]:
    result = await db.execute(select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc()))
    return list(result.scalars().all())


async def get_customer_order(db: AsyncSession, user_id, order_number: str) -> Order | None:
    result = await db.execute(select(Order).where(Order.user_id == user_id, Order.order_number == order_number))
    return result.scalar_one_or_none()


async def get_admin_orders(db: AsyncSession, limit: int = 50, offset: int = 0) -> list[Order]:
    result = await db.execute(select(Order).order_by(Order.created_at.desc()).limit(limit).offset(offset))
    return list(result.scalars().all())


async def get_admin_order(db: AsyncSession, order_number: str) -> Order | None:
    result = await db.execute(select(Order).where(Order.order_number == order_number))
    return result.scalar_one_or_none()


async def serialize_order(db: AsyncSession, order: Order) -> dict:
    items = await _order_items(db, order.id)
    payments = await _payments(db, order.id)
    shipments = await _shipments(db, order.id)
    address_result = await db.execute(select(OrderAddress).where(OrderAddress.order_id == order.id))
    addresses = {address.address_type: address for address in address_result.scalars().all()}

    def serialize_address(address):
        if address is None:
            return None
        return {
            "id": address.id,
            "address_type": address.address_type,
            "recipient_name": address.recipient_name,
            "phone": address.phone,
            "line1": address.line1,
            "line2": address.line2,
            "locality": address.locality,
            "city": address.city,
            "state_or_region": address.state_or_region,
            "postal_code": address.postal_code,
            "country_code": address.country_code,
            "delivery_instructions": address.delivery_instructions,
        }
    return {
        "id": order.id,
        "order_number": order.order_number,
        "status": order.status,
        "payment_status": order.payment_status,
        "fulfilment_status": order.fulfilment_status,
        "currency": order.currency,
        "subtotal_minor": order.subtotal_minor,
        "discount_minor": order.discount_minor,
        "shipping_minor": order.shipping_minor,
        "tax_minor": order.tax_minor,
        "total_minor": order.total_minor,
        "customer_id": order.user_id,
        "contact_email": order.contact_email,
        "placed_at": order.placed_at,
        "items": items,
        "payments": payments,
        "shipments": shipments,
        "shipping_address": serialize_address(addresses.get("SHIPPING")),
        "billing_address": serialize_address(addresses.get("BILLING")),
    }


async def add_order_status_event(db: AsyncSession, order: Order, to_status: str, message: str | None = None, actor_type: str = "SYSTEM", actor_user_id=None, is_customer_visible: bool = True) -> OrderStatusEvent:
    event = OrderStatusEvent(
        order_id=order.id,
        from_status=order.status,
        to_status=to_status,
        message=message,
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        is_customer_visible=is_customer_visible,
    )
    order.status = to_status
    db.add(event)
    await db.flush()
    return event


async def update_order_status(db: AsyncSession, order: Order, to_status: str, message: str | None, actor_user_id, is_customer_visible: bool = True) -> Order:
    normalized_status = to_status.upper()
    if normalized_status not in ORDER_STATUSES:
        raise ValueError(f"Unsupported order status: {to_status}")
    if normalized_status in {"FULFILLED", "DELIVERED"}:
        order.fulfilment_status = "FULFILLED"
    if normalized_status == order.status:
        return order
    await add_order_status_event(db, order, to_status=normalized_status, message=message, actor_type="ADMIN", actor_user_id=actor_user_id, is_customer_visible=is_customer_visible)
    if normalized_status == "CANCELLED":
        await release_order_reservations(db, order.id, note="Order cancelled by admin")
    return order


async def create_payment_for_order(db: AsyncSession, order: Order, provider: str, idempotency_key: str, provider_order_id: str | None = None) -> Payment:
    payment = Payment(
        order_id=order.id,
        provider=provider,
        status="PENDING",
        amount_minor=order.total_minor,
        currency=order.currency,
        idempotency_key=idempotency_key,
        provider_order_id=provider_order_id,
    )
    db.add(payment)
    await db.flush()
    order.payment_status = "PENDING"
    return payment


async def set_payment_provider_order_id(db: AsyncSession, payment: Payment, provider_order_id: str | None) -> Payment:
    payment.provider_order_id = provider_order_id or payment.provider_order_id
    return payment


async def mark_payment_captured(db: AsyncSession, payment: Payment, provider_payment_id: str | None = None) -> Payment:
    payment.status = "CAPTURED"
    payment.provider_payment_id = provider_payment_id or payment.provider_payment_id
    payment.captured_at = datetime.now(timezone.utc)
    await consume_order_reservations(db, payment.order_id)
    result = await db.execute(select(Order).where(Order.id == payment.order_id))
    order = result.scalar_one_or_none()
    if order is not None:
        order.payment_status = "PAID"
    return payment


async def mark_payment_failed(db: AsyncSession, payment: Payment, failure_code: str | None = None, failure_message: str | None = None) -> Payment:
    payment.status = "FAILED"
    payment.failure_code = failure_code or payment.failure_code
    payment.failure_message = failure_message or payment.failure_message
    await release_order_reservations(db, payment.order_id)
    result = await db.execute(select(Order).where(Order.id == payment.order_id))
    order = result.scalar_one_or_none()
    if order is not None:
        order.payment_status = "FAILED"
    return payment


async def expire_pending_payments(db: AsyncSession, timeout_minutes: int = 15) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
    result = await db.execute(select(Payment).where(Payment.status == "PENDING", Payment.created_at <= cutoff).with_for_update(skip_locked=True))
    expired = 0
    for payment in result.scalars().all():
        await mark_payment_failed(db, payment, failure_code="PAYMENT_TIMEOUT", failure_message=f"Payment expired after {timeout_minutes} minutes")
        order_result = await db.execute(select(Order).where(Order.id == payment.order_id))
        order = order_result.scalar_one_or_none()
        if order is not None:
            await add_order_status_event(db, order, to_status="PAYMENT_FAILED", message="Payment window expired; inventory released", actor_type="SYSTEM", is_customer_visible=True)
        expired += 1
    if expired:
        await db.commit()
    return expired


async def create_shipment(db: AsyncSession, order: Order, carrier: str, tracking_number: str | None, tracking_url: str | None, item_quantities: dict) -> Shipment:
    shipment = Shipment(order_id=order.id, carrier=carrier, tracking_number=tracking_number, tracking_url=tracking_url, status="CREATED")
    db.add(shipment)
    await db.flush()
    items = await _order_items(db, order.id)
    for item in items:
        qty = item_quantities.get(item.id, 0)
        if qty > 0:
            db.add(ShipmentItem(shipment_id=shipment.id, order_item_id=item.id, quantity=qty))
    order.fulfilment_status = "PARTIALLY_FULFILLED" if item_quantities else order.fulfilment_status
    return shipment


async def create_refund(
    db: AsyncSession,
    payment: Payment,
    amount_minor: int,
    reason: str | None = None,
    provider_refund_id: str | None = None,
) -> Refund:
    await validate_refund(db, payment, amount_minor)

    total_result = await db.execute(select(func.coalesce(func.sum(Refund.amount_minor), 0)).where(Refund.payment_id == payment.id, Refund.status == "SUCCEEDED"))
    refunded_minor = total_result.scalar_one()
    refund = Refund(
        payment_id=payment.id,
        order_id=payment.order_id,
        provider_refund_id=provider_refund_id,
        amount_minor=amount_minor,
        currency=payment.currency,
        status="SUCCEEDED",
        reason=reason,
        completed_at=datetime.now(timezone.utc),
    )
    payment.status = "PARTIALLY_REFUNDED" if refunded_minor + amount_minor < payment.amount_minor else "REFUNDED"
    result = await db.execute(select(Order).where(Order.id == payment.order_id))
    order = result.scalar_one_or_none()
    if order is not None:
        order.payment_status = payment.status
    db.add(refund)
    return refund


async def validate_refund(db: AsyncSession, payment: Payment, amount_minor: int) -> None:
    if amount_minor <= 0:
        raise ValueError("Refund amount must be greater than zero")
    if payment.status not in {"CAPTURED", "PARTIALLY_REFUNDED"}:
        raise ValueError("Only captured payments can be refunded")
    total_result = await db.execute(select(func.coalesce(func.sum(Refund.amount_minor), 0)).where(Refund.payment_id == payment.id, Refund.status == "SUCCEEDED"))
    refunded_minor = total_result.scalar_one()
    if refunded_minor + amount_minor > payment.amount_minor:
        raise ValueError("Refund amount exceeds the remaining captured amount")
