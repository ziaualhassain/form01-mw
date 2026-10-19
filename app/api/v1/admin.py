from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import Principal, require_admin
from app.db.session import get_db
from app.schemas import HomepageSectionRead, HomepageSectionWrite, InventoryAdjustRequest, ProductCreate, ProductRead, ProductUpdate, OrderRead, OrderStatusUpdateRequest, ShipmentCreateRequest, RefundCreateRequest, VariantRead
from app.services.orders import expire_pending_payments, get_admin_order, get_admin_orders, serialize_order, create_shipment, create_refund, update_order_status, validate_refund
from app.services.admin import create_product, delete_product, list_admin_products, publish_product, unpublish_product, update_product
from app.services.payments import create_razorpay_refund
from app.services.inventory import adjust_stock
from app.services.homepage import get_admin_homepage, replace_homepage_sections


router = APIRouter()


@router.get("/homepage", response_model=list[HomepageSectionRead])
async def admin_homepage(principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    sections = await get_admin_homepage(db)
    return [HomepageSectionRead(id=section.id, section_key=section.section_key, section_type=section.section_type, position=section.position, enabled=section.enabled, content=section.content_json, publish_at=section.publish_at, unpublish_at=section.unpublish_at) for section in sections]


@router.put("/homepage", response_model=list[HomepageSectionRead])
async def admin_replace_homepage(payload: list[HomepageSectionWrite], principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    try:
        sections = await replace_homepage_sections(db, [item.model_dump() for item in payload], principal.user_id)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return [HomepageSectionRead(id=section.id, section_key=section.section_key, section_type=section.section_type, position=section.position, enabled=section.enabled, content=section.content_json, publish_at=section.publish_at, unpublish_at=section.unpublish_at) for section in sections]


@router.post("/payments/expire-pending")
async def admin_expire_pending_payments(principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    expired = await expire_pending_payments(db)
    return {"expired_payments": expired}


@router.post("/inventory/adjust")
async def admin_adjust_inventory(payload: InventoryAdjustRequest, principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    try:
        level = await adjust_stock(db, payload.variant_id, payload.quantity_delta, payload.note)
        await db.commit()
        await db.refresh(level)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"id": level.id, "variant_id": level.variant_id, "on_hand": level.on_hand, "reserved": level.reserved, "available": level.on_hand - level.reserved}


@router.get("/products", response_model=list[ProductRead])
async def admin_products(principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    items = await list_admin_products(db)
    from app.services.inventory import list_variant_inventory
    inventory = await list_variant_inventory(db, [item.id for item in items])
    return [ProductRead(id=item.id, title=item.title, slug=item.slug, description=item.description, image_url=item.image_url, image_urls=item.image_urls, features=item.features, gender=item.gender, theme=item.theme, product_type=item.product_type, status=item.status, variants=[VariantRead(**variant) for variant in inventory.get(item.id, [])]) for item in items]


@router.post("/products", response_model=ProductRead, status_code=201)
async def admin_create_product(payload: ProductCreate, principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    try:
        product = await create_product(db, **payload.model_dump(), created_by=principal.user_id)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Product slug or variant SKU already exists") from exc
    from app.services.inventory import list_variant_inventory
    inventory = await list_variant_inventory(db, [product.id])
    return ProductRead(id=product.id, title=product.title, slug=product.slug, description=product.description, image_url=product.image_url, image_urls=product.image_urls, features=product.features, gender=product.gender, theme=product.theme, product_type=product.product_type, status=product.status, variants=[VariantRead(**variant) for variant in inventory.get(product.id, [])])


@router.patch("/products/{product_id}", response_model=ProductRead)
async def admin_update_product(product_id: str, payload: ProductUpdate, principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    try:
        product = await update_product(db, product_id, **payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Product slug or variant SKU already exists") from exc
    from app.services.inventory import list_variant_inventory
    inventory = await list_variant_inventory(db, [product.id])
    return ProductRead(id=product.id, title=product.title, slug=product.slug, description=product.description, image_url=product.image_url, image_urls=product.image_urls, features=product.features, gender=product.gender, theme=product.theme, product_type=product.product_type, status=product.status, variants=[VariantRead(**variant) for variant in inventory.get(product.id, [])])


@router.post("/products/{product_id}/publish", response_model=ProductRead)
async def admin_publish_product(product_id: str, principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    try:
        product = await publish_product(db, product_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ProductRead(id=product.id, title=product.title, slug=product.slug, description=product.description, image_url=product.image_url, image_urls=product.image_urls, features=product.features, gender=product.gender, theme=product.theme, product_type=product.product_type, status=product.status)


@router.post("/products/{product_id}/unpublish", response_model=ProductRead)
async def admin_unpublish_product(product_id: str, principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    try:
        product = await unpublish_product(db, product_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ProductRead(id=product.id, title=product.title, slug=product.slug, description=product.description, image_url=product.image_url, image_urls=product.image_urls, features=product.features, gender=product.gender, theme=product.theme, product_type=product.product_type, status=product.status)


@router.delete("/products/{product_id}", status_code=204)
async def admin_delete_product(product_id: str, principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    try:
        await delete_product(db, product_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/orders", response_model=list[OrderRead])
async def admin_orders(principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    orders = await get_admin_orders(db)
    return [OrderRead(**await serialize_order(db, order)) for order in orders]


@router.get("/orders/{order_number}", response_model=OrderRead)
async def admin_order(order_number: str, principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    order = await get_admin_order(db, order_number)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return OrderRead(**await serialize_order(db, order))


@router.patch("/orders/{order_number}/status", response_model=OrderRead)
async def admin_update_order_status(order_number: str, payload: OrderStatusUpdateRequest, principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    order = await get_admin_order(db, order_number)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    try:
        await update_order_status(db, order, payload.status, payload.message, principal.user_id, payload.is_customer_visible)
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return OrderRead(**await serialize_order(db, order))


@router.post("/orders/{order_number}/shipments")
async def admin_create_shipment(order_number: str, payload: ShipmentCreateRequest, principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    order = await get_admin_order(db, order_number)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    shipment = await create_shipment(db, order, payload.carrier, payload.tracking_number, payload.tracking_url, payload.item_quantities)
    await db.commit()
    await db.refresh(shipment)
    return {"id": shipment.id, "order_number": order_number, "status": shipment.status}


@router.post("/payments/{payment_id}/refund")
async def admin_refund(payment_id: str, payload: RefundCreateRequest, principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    from app.db.models import Payment
    from sqlalchemy import select

    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment not found")
    amount = payload.amount_minor or payment.amount_minor
    provider_refund_id = None
    try:
        await validate_refund(db, payment, amount)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if payment.provider == "razorpay":
        if not payment.provider_payment_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment has no Razorpay payment ID")
        try:
            provider_refund = create_razorpay_refund(payment_id=payment.provider_payment_id, amount_minor=amount, reason=payload.reason)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Razorpay refund failed") from exc
        provider_refund_id = provider_refund.get("id")
    try:
        refund = await create_refund(db, payment, amount, payload.reason, provider_refund_id=provider_refund_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(refund)
    return {"id": refund.id, "status": refund.status, "amount_minor": refund.amount_minor}
