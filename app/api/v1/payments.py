from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentPrincipal
from app.db.models import Order, Payment
from app.db.session import get_db
from app.schemas import RazorpayPaymentVerifyRequest
from app.services.orders import add_order_status_event, mark_payment_captured
from app.services.payments import fetch_razorpay_payment, verify_razorpay_payment_signature


router = APIRouter()


@router.post("/razorpay/verify")
async def verify_razorpay_payment(payload: RazorpayPaymentVerifyRequest, principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Payment).join(Order, Order.id == Payment.order_id).where(
            Payment.provider == "razorpay",
            Payment.provider_order_id == payload.razorpay_order_id,
            Order.user_id == principal.user_id,
        ).with_for_update(of=Payment)
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")

    if payment.status == "CAPTURED":
        if payment.provider_payment_id == payload.razorpay_payment_id:
            existing_order = await db.execute(select(Order).where(Order.id == payment.order_id))
            order = existing_order.scalar_one_or_none()
            return {"data": {"verified": True, "payment_id": str(payment.id), "order_number": order.order_number if order else None}}
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Payment is already captured with another provider payment")

    try:
        signature_valid = verify_razorpay_payment_signature(
            razorpay_order_id=payload.razorpay_order_id,
            razorpay_payment_id=payload.razorpay_payment_id,
            razorpay_signature=payload.razorpay_signature,
        )
        provider_payment = fetch_razorpay_payment(payload.razorpay_payment_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to validate Razorpay payment") from exc

    if not signature_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Razorpay signature")
    if provider_payment.get("order_id") != payment.provider_order_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Razorpay order mismatch")
    if provider_payment.get("amount") != payment.amount_minor or provider_payment.get("currency") != payment.currency:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Razorpay amount or currency mismatch")
    if provider_payment.get("status") not in {"authorized", "captured"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Razorpay payment is not successful")

    try:
        await mark_payment_captured(db, payment, provider_payment_id=payload.razorpay_payment_id)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    order_result = await db.execute(select(Order).where(Order.id == payment.order_id))
    order = order_result.scalar_one_or_none()
    if order is not None:
        await add_order_status_event(
            db,
            order,
            to_status="CONFIRMED",
            message="Payment verified through Razorpay checkout",
            actor_type="CUSTOMER",
            actor_user_id=principal.user_id,
            is_customer_visible=True,
        )
    await db.commit()
    return {"data": {"verified": True, "payment_id": str(payment.id), "order_number": order.order_number if order else None}}