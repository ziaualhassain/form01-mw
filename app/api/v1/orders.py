from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentPrincipal
from app.db.session import get_db
from app.schemas import OrderRead
from app.services.orders import get_customer_order, get_customer_orders, serialize_order, add_order_status_event
from app.services.inventory import release_order_reservations
from app.services.coupons import finish_redemption
from app.db.models import Payment
from sqlalchemy import select
from app.services.orders import mark_payment_failed


router = APIRouter()


@router.get("/me/orders", response_model=list[OrderRead])
async def me_orders(principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    orders = await get_customer_orders(db, principal.user_id)
    return [OrderRead(**await serialize_order(db, order)) for order in orders]


@router.get("/me/orders/{order_number}", response_model=OrderRead)
async def me_order(order_number: str, principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    order = await get_customer_order(db, principal.user_id, order_number)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return OrderRead(**await serialize_order(db, order))


@router.post("/me/orders/{order_number}/cancel-request")
async def cancel_request(order_number: str, principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    order = await get_customer_order(db, principal.user_id, order_number)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if order.status in {"CANCELLED", "FULFILLED"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Order cannot be cancelled")
    payments = (await db.execute(select(Payment).where(Payment.order_id == order.id, Payment.status == "PENDING").with_for_update())).scalars().all()
    for payment in payments:
        await mark_payment_failed(db, payment, failure_code="ORDER_CANCELLED")
    await release_order_reservations(db, order.id, note="Customer cancelled order")
    await finish_redemption(db, order.id, "RELEASED")
    await add_order_status_event(db, order, to_status="CANCELLED", message="Customer cancel request accepted", actor_type="CUSTOMER", actor_user_id=principal.user_id, is_customer_visible=True)
    await db.commit()
    return {"order_number": order.order_number, "status": order.status}