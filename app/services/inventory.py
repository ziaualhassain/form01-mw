from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InventoryLevel, InventoryLocation, InventoryMovement, InventoryReservation, ProductVariant


async def get_available_quantities(db: AsyncSession, variant_ids: list) -> dict:
    if not variant_ids:
        return {}
    result = await db.execute(
        select(
            InventoryLevel.variant_id,
            func.coalesce(func.sum(InventoryLevel.on_hand), 0),
            func.coalesce(func.sum(InventoryLevel.reserved), 0),
        )
        .where(InventoryLevel.variant_id.in_(variant_ids))
        .group_by(InventoryLevel.variant_id)
    )
    return {
        row[0]: {"on_hand": row[1], "reserved": row[2], "available": row[1] - row[2]}
        for row in result.all()
    }


async def list_variant_inventory(db: AsyncSession, product_ids: list) -> dict:
    if not product_ids:
        return {}
    inventory = (
        select(
            InventoryLevel.variant_id,
            func.coalesce(func.sum(InventoryLevel.on_hand), 0).label("on_hand"),
            func.coalesce(func.sum(InventoryLevel.reserved), 0).label("reserved"),
        )
        .group_by(InventoryLevel.variant_id)
        .subquery()
    )
    result = await db.execute(
        select(ProductVariant, inventory.c.on_hand, inventory.c.reserved)
        .outerjoin(inventory, inventory.c.variant_id == ProductVariant.id)
        .where(ProductVariant.product_id.in_(product_ids), ProductVariant.status == "ACTIVE")
    )
    variants = {}
    for variant, on_hand, reserved in result.all():
        on_hand = on_hand or 0
        reserved = reserved or 0
        variants.setdefault(variant.product_id, []).append(
            {
                "id": variant.id,
                "product_id": variant.product_id,
                "sku": variant.sku,
                "title": variant.title,
                "price_minor": variant.price_minor,
                "currency": variant.currency,
                "status": variant.status,
                "on_hand": on_hand,
                "reserved": reserved,
                "available": on_hand - reserved,
            }
        )
    return variants


async def adjust_stock(db: AsyncSession, variant_id, quantity_delta: int, note: str | None = None) -> InventoryLevel:
    if quantity_delta == 0:
        raise ValueError("Stock adjustment cannot be zero")
    location_result = await db.execute(select(InventoryLocation).where(InventoryLocation.code == "DEFAULT", InventoryLocation.status == "ACTIVE"))
    location = location_result.scalar_one_or_none()
    if location is None:
        location = InventoryLocation(code="DEFAULT", name="Default warehouse", status="ACTIVE")
        db.add(location)
        await db.flush()

    level_result = await db.execute(
        select(InventoryLevel)
        .where(InventoryLevel.location_id == location.id, InventoryLevel.variant_id == variant_id)
        .with_for_update()
    )
    level = level_result.scalar_one_or_none()
    if level is None:
        level = InventoryLevel(location_id=location.id, variant_id=variant_id, on_hand=0, reserved=0)
        db.add(level)
        await db.flush()
    if level.on_hand + quantity_delta < level.reserved:
        raise ValueError("Stock cannot be lower than currently reserved quantity")
    level.on_hand += quantity_delta
    db.add(InventoryMovement(inventory_level_id=level.id, variant_id=variant_id, movement_type="ADJUSTMENT", quantity_delta=quantity_delta, note=note))
    return level


async def reserve_inventory(db: AsyncSession, order_id, quantities: dict) -> None:
    for variant_id, quantity in sorted(quantities.items(), key=lambda item: str(item[0])):
        remaining = quantity
        levels_result = await db.execute(
            select(InventoryLevel)
            .where(InventoryLevel.variant_id == variant_id)
            .order_by(InventoryLevel.id)
            .with_for_update()
        )
        levels = levels_result.scalars().all()
        for level in levels:
            available = level.on_hand - level.reserved
            allocation = min(available, remaining)
            if allocation <= 0:
                continue
            level.reserved += allocation
            db.add(InventoryReservation(inventory_level_id=level.id, variant_id=variant_id, order_id=order_id, quantity=allocation, status="ACTIVE"))
            remaining -= allocation
            if remaining == 0:
                break
        if remaining:
            raise ValueError(f"Insufficient inventory for variant {variant_id}")


async def consume_order_reservations(db: AsyncSession, order_id) -> None:
    result = await db.execute(
        select(InventoryReservation, InventoryLevel)
        .join(InventoryLevel, InventoryLevel.id == InventoryReservation.inventory_level_id)
        .where(InventoryReservation.order_id == order_id, InventoryReservation.status == "ACTIVE")
        .with_for_update()
    )
    now = datetime.now(timezone.utc)
    for reservation, level in result.all():
        level.reserved -= reservation.quantity
        level.on_hand -= reservation.quantity
        reservation.status = "CONSUMED"
        reservation.consumed_at = now
        db.add(InventoryMovement(inventory_level_id=level.id, variant_id=reservation.variant_id, order_id=order_id, movement_type="SALE", quantity_delta=-reservation.quantity, note="Order payment captured"))


async def release_order_reservations(db: AsyncSession, order_id, note: str = "Order payment failed") -> None:
    result = await db.execute(
        select(InventoryReservation, InventoryLevel)
        .join(InventoryLevel, InventoryLevel.id == InventoryReservation.inventory_level_id)
        .where(InventoryReservation.order_id == order_id, InventoryReservation.status == "ACTIVE")
        .with_for_update()
    )
    now = datetime.now(timezone.utc)
    for reservation, level in result.all():
        level.reserved -= reservation.quantity
        reservation.status = "RELEASED"
        reservation.released_at = now
        db.add(InventoryMovement(inventory_level_id=level.id, variant_id=reservation.variant_id, order_id=order_id, movement_type="RELEASE", quantity_delta=0, note=note))