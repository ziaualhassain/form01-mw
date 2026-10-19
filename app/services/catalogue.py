from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Product


async def list_products(db: AsyncSession, limit: int = 20, offset: int = 0, gender: str | None = None, theme: str | None = None, product_type: str | None = None) -> list[Product]:
    query = select(Product).where(Product.status == "ACTIVE")
    if gender:
        query = query.where(Product.gender == gender)
    if theme:
        query = query.where(Product.theme == theme)
    if product_type:
        query = query.where(Product.product_type == product_type)
    result = await db.execute(query.order_by(Product.created_at.desc()).limit(limit).offset(offset))
    return list(result.scalars().all())


async def get_product_by_slug(db: AsyncSession, slug: str) -> Product | None:
    result = await db.execute(select(Product).where(Product.slug == slug))
    return result.scalar_one_or_none()
