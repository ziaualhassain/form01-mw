from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentPrincipal
from app.api.gst_test import require_local_gst_checkout
from app.core.config import get_settings
from app.db.session import get_db
from app.schemas import CheckoutCreateRequest, CheckoutCreateResponse, CheckoutQuoteRequest, CheckoutQuoteResponse
from app.services.cart import get_or_create_cart
from app.services.checkout import place_order, quote_cart


router = APIRouter()


@router.post("/checkout/quote", response_model=CheckoutQuoteResponse, dependencies=[Depends(require_local_gst_checkout)])
async def checkout_quote(payload: CheckoutQuoteRequest, principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    cart = await get_or_create_cart(db, principal.user_id)
    try:
        quote = await quote_cart(db, cart.id, principal.user_id, payload.coupon_code, payload.shipping_address_id)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CheckoutQuoteResponse(**quote)


@router.post("/checkout", response_model=CheckoutCreateResponse, status_code=201, dependencies=[Depends(require_local_gst_checkout)])
async def checkout(payload: CheckoutCreateRequest, principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    cart = await get_or_create_cart(db, principal.user_id)
    settings = get_settings()
    payment_provider = payload.payment_provider or ("razorpay" if settings.razorpay_configured else "mock")
    try:
        order, payment = await place_order(
            db,
            cart.id,
            contact_email=principal.email,
            user_id=principal.user_id,
            shipping_address_id=payload.shipping_address_id,
            billing_address_id=payload.billing_address_id,
            payment_provider=payment_provider,
            idempotency_key=payload.idempotency_key,
            coupon_code=payload.coupon_code,
            gst_test_version=payload.gst_test_version,
        )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CheckoutCreateResponse(
        gst_test=(order.draft_invoice_snapshot or {}).get("gst_test"),
        order_id=order.id,
        order_number=order.order_number,
        payment_status=order.payment_status,
        total_minor=order.total_minor,
        payment_id=payment.id,
        payment_provider=payment.provider,
        provider_order_id=payment.provider_order_id,
        provider_key_id=settings.razorpay_key_id if payment.provider == "razorpay" else None,
    )
