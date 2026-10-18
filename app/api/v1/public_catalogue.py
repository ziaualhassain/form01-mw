from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.schemas import ProductRead, StorefrontConfig, VariantRead
from app.services.catalogue import get_product_by_slug, list_products
from app.services.inventory import list_variant_inventory


router = APIRouter()


@router.get("/products", response_model=list[ProductRead])
async def products(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    items = await list_products(db, limit=limit, offset=offset)
    inventory = await list_variant_inventory(db, [item.id for item in items])
    return [
        ProductRead(
            id=item.id,
            title=item.title,
            slug=item.slug,
            description=item.description,
            image_url=item.image_url,
            image_urls=item.image_urls,
            status=item.status,
            variants=[VariantRead(**variant) for variant in inventory.get(item.id, [])],
        )
        for item in items
    ]


@router.get("/products/{slug}", response_model=ProductRead)
async def product_detail(slug: str, db: AsyncSession = Depends(get_db)):
    item = await get_product_by_slug(db, slug)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    inventory = await list_variant_inventory(db, [item.id])
    return ProductRead(
        id=item.id,
        title=item.title,
        slug=item.slug,
        description=item.description,
        image_url=item.image_url,
        image_urls=item.image_urls,
        status=item.status,
        variants=[VariantRead(**variant) for variant in inventory.get(item.id, [])],
    )


@router.get("/storefront/config", response_model=StorefrontConfig)
async def storefront_config():
    settings = get_settings()
    return StorefrontConfig(app_name=settings.app_name, environment=settings.environment)
