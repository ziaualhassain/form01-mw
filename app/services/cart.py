from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Cart, CartItem
from app.services.inventory import get_available_quantities


async def get_or_create_cart(db: AsyncSession, user_id) -> Cart:
    result = await db.execute(select(Cart).where(Cart.user_id == user_id, Cart.status == "ACTIVE"))
    cart = result.scalar_one_or_none()
    if cart is None:
        cart = Cart(user_id=user_id)
        db.add(cart)
        await db.flush()
    return cart


async def list_cart_items(db: AsyncSession, cart_id) -> list[CartItem]:
    result = await db.execute(select(CartItem).where(CartItem.cart_id == cart_id))
    return list(result.scalars().all())


async def reconcile_cart_inventory(db: AsyncSession, cart_id, commit: bool = True) -> list[CartItem]:
    items = await list_cart_items(db, cart_id)
    availability = await get_available_quantities(db, [item.variant_id for item in items])
    changed = False
    for item in items:
        available = availability.get(item.variant_id, {}).get("available", 0)
        if available <= 0:
            await db.delete(item)
            changed = True
        elif item.quantity > available:
            item.quantity = available
            changed = True
    if changed:
        await db.flush()
        if commit:
            await db.commit()
    return await list_cart_items(db, cart_id)


async def upsert_cart_item(db: AsyncSession, cart_id, variant_id, quantity: int) -> CartItem:
    result = await db.execute(select(CartItem).where(CartItem.cart_id == cart_id, CartItem.variant_id == variant_id))
    item = result.scalar_one_or_none()
    if item is None:
        item = CartItem(cart_id=cart_id, variant_id=variant_id, quantity=quantity)
        db.add(item)
    else:
        item.quantity = quantity
    await db.commit()
    await db.refresh(item)
    return item
