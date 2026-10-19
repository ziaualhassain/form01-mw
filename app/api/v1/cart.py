from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentPrincipal
from app.db.models import CartItem, ProductVariant
from app.db.session import get_db
from app.schemas import CartItemCreate, CartItemRead, CartRead
from app.services.cart import get_or_create_cart, list_cart_items, reconcile_cart_inventory, upsert_cart_item
from app.services.inventory import get_available_quantities


router = APIRouter()


@router.get("/cart", response_model=CartRead)
async def get_cart(principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    cart = await get_or_create_cart(db, principal.user_id)
    items = await reconcile_cart_inventory(db, cart.id)
    variant_ids = [item.variant_id for item in items]
    availability = await get_available_quantities(db, variant_ids)
    prices = {}
    if variant_ids:
        result = await db.execute(select(ProductVariant.id, ProductVariant.price_minor).where(ProductVariant.id.in_(variant_ids)))
        prices = {row[0]: row[1] for row in result.all()}
    total = sum(prices.get(item.variant_id, 0) * item.quantity for item in items)
    return CartRead(
        id=cart.id,
        currency=cart.currency,
        items=[CartItemRead(id=item.id, variant_id=item.variant_id, quantity=item.quantity, available_quantity=availability.get(item.variant_id, {}).get("available", 0)) for item in items],
        subtotal_minor=total,
        total_minor=total,
    )


@router.post("/cart/items", response_model=CartItemRead, status_code=201)
async def add_cart_item(payload: CartItemCreate, principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    cart = await get_or_create_cart(db, principal.user_id)
    availability = await get_available_quantities(db, [payload.variant_id])
    available = availability.get(payload.variant_id, {}).get("available", 0)
    if payload.quantity > available:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Only {available} units are currently available")
    item = await upsert_cart_item(db, cart.id, payload.variant_id, payload.quantity)
    return CartItemRead(id=item.id, variant_id=item.variant_id, quantity=item.quantity, available_quantity=available)


@router.delete("/cart/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_cart_item(item_id: UUID, principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    cart = await get_or_create_cart(db, principal.user_id)
    result = await db.execute(select(CartItem).where(CartItem.id == item_id, CartItem.cart_id == cart.id))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart item not found")
    await db.delete(item)
    await db.commit()
