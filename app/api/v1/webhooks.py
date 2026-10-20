from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.db.models import Order, Payment, PaymentEvent
from app.db.session import async_session_factory
from app.services.orders import add_order_status_event, mark_payment_captured, mark_payment_failed
from app.services.payments import verify_razorpay_webhook_signature

router = APIRouter()


@router.post("/{provider}")
async def provider_webhook(provider: str, request: Request):
    body = await request.body()
    payload = json.loads(body.decode("utf-8") or "{}")
    payment_entity: dict = {}

    if provider == "razorpay":
        signature = request.headers.get("X-Razorpay-Signature") or request.headers.get("x-razorpay-signature")
        if not signature:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Razorpay signature")
        try:
            if not verify_razorpay_webhook_signature(body=body, signature=signature):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Razorpay signature")
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
        provider_event_id = hashlib.sha256(body).hexdigest()
        event_type = payload.get("event")
        payment_entity = (payload.get("payload") or {}).get("payment", {}).get("entity", {})
        provider_payment_id = payment_entity.get("id")
        provider_order_id = payment_entity.get("order_id")
    else:
        provider_event_id = payload.get("provider_event_id")
        event_type = payload.get("event_type")
        provider_payment_id = payload.get("provider_payment_id")
        provider_order_id = payload.get("provider_order_id")
        payment_entity = payload

    if not provider_event_id or not event_type:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="provider_event_id and event_type are required")

    async with async_session_factory() as db:
        existing = await db.execute(select(PaymentEvent).where(PaymentEvent.provider == provider, PaymentEvent.provider_event_id == provider_event_id))
        if existing.scalar_one_or_none() is not None:
            return {"data": {"provider": provider, "received": True, "duplicate": True}, "meta": {"request_id": getattr(request.state, "request_id", None)}}

        payment = None
        payment_id = payload.get("payment_id")
        if payment_id:
            result = await db.execute(select(Payment).where(Payment.id == payment_id))
            payment = result.scalar_one_or_none()
        elif provider_payment_id:
            result = await db.execute(select(Payment).where(Payment.provider == provider, Payment.provider_payment_id == provider_payment_id))
            payment = result.scalar_one_or_none()
        if payment is None and provider_order_id:
            result = await db.execute(select(Payment).where(Payment.provider == provider, Payment.provider_order_id == provider_order_id))
            payment = result.scalar_one_or_none()
        if payment and provider == "razorpay" and event_type.startswith("payment."):
            if payment_entity.get("order_id") != payment.provider_order_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Razorpay order mismatch")
            if payment_entity.get("amount") not in (None, payment.amount_minor) or payment_entity.get("currency") not in (None, payment.currency):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Razorpay amount or currency mismatch")
        event = PaymentEvent(
            provider=provider,
            provider_event_id=provider_event_id,
            payment_id=payment.id if payment else None,
            event_type=event_type,
            payload_json=body.decode("utf-8"),
            processing_status="RECEIVED",
        )
        if payment is not None:
            await db.refresh(payment, with_for_update=True)
            duplicate = (await db.execute(select(PaymentEvent).where(PaymentEvent.provider == provider, PaymentEvent.provider_event_id == provider_event_id))).scalar_one_or_none()
            if duplicate is not None:
                return {"data": {"provider": provider, "received": True, "duplicate": True}, "meta": {"request_id": getattr(request.state, "request_id", None)}}
        db.add(event)
        if payment and event_type in {"payment.captured", "payment.succeeded", "payment_succeeded", "payment.authorized"}:
            already_captured = payment.status == "CAPTURED"
            try:
                await mark_payment_captured(db, payment, provider_payment_id=provider_payment_id)
            except ValueError as exc:
                await db.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            order_result = await db.execute(select(Order).where(Order.id == payment.order_id))
            order = order_result.scalar_one_or_none()
            if order is not None and not already_captured:
                await add_order_status_event(db, order, to_status="CONFIRMED", message="Payment confirmed by webhook", actor_type="SYSTEM", is_customer_visible=True)
        elif payment and payment.status == "PENDING" and event_type in {"payment.failed", "payment_failed"}:
            failure_message = payment_entity.get("error_description") or payment_entity.get("error_reason")
            await mark_payment_failed(db, payment, failure_code=payment_entity.get("error_code"), failure_message=failure_message)
            order_result = await db.execute(select(Order).where(Order.id == payment.order_id))
            order = order_result.scalar_one_or_none()
            if order is not None:
                await add_order_status_event(db, order, to_status="PAYMENT_FAILED", message=failure_message or "Payment failed", actor_type="SYSTEM", is_customer_visible=True)
        event.processing_status = "PROCESSED"
        event.processed_at = datetime.now(timezone.utc)
        await db.commit()
    return {"data": {"provider": provider, "received": True}, "meta": {"request_id": getattr(request.state, "request_id", None)}}
