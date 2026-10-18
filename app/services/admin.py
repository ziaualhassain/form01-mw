from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Product, ProductVariant
from app.services.inventory import adjust_stock


async def list_admin_products(db: AsyncSession) -> list[Product]:
    result = await db.execute(select(Product).order_by(Product.created_at.desc()))
    return list(result.scalars().all())


async def create_product(
    db: AsyncSession,
    *,
    title: str,
    slug: str,
    description: str | None,
    image_url: str | None,
    image_urls: list[str],
    features: list[str],
    product_type: str | None,
    seo_title: str | None,
    seo_description: str | None,
    sku: str | None,
    price_minor: int | None,
    currency: str,
    initial_stock: int,
    variants: list[dict],
    created_by=None,
) -> Product:
    existing_product = await db.execute(select(Product).where(Product.slug == slug))
    if existing_product.scalar_one_or_none() is not None:
        raise ValueError(f"A product with slug '{slug}' already exists")
    if variants:
        sku = None
    if sku and price_minor is None:
        raise ValueError("price_minor is required when sku is provided")
    if price_minor is not None and not sku:
        raise ValueError("sku is required when price_minor is provided")
    if sku:
        existing_variant = await db.execute(select(ProductVariant).where(ProductVariant.sku == sku))
        if existing_variant.scalar_one_or_none() is not None:
            raise ValueError(f"A variant with SKU '{sku}' already exists")
    normalized_variants = variants or ([{"size": "Default", "sku": sku, "price_minor": price_minor, "currency": currency, "initial_stock": initial_stock}] if sku and price_minor is not None else [])
    variant_skus = [variant_payload["sku"].strip() for variant_payload in normalized_variants]
    duplicate_skus = sorted({variant_sku for variant_sku in variant_skus if variant_skus.count(variant_sku) > 1})
    if duplicate_skus:
        raise ValueError(f"Duplicate SKU in variant rows: {', '.join(duplicate_skus)}")
    for variant_payload in normalized_variants:
        existing_variant = await db.execute(select(ProductVariant).where(ProductVariant.sku == variant_payload["sku"]))
        if existing_variant.scalar_one_or_none() is not None:
            raise ValueError(f"A variant with SKU '{variant_payload['sku']}' already exists")
    normalized_image_urls = [url.strip() for url in image_urls if url.strip()]
    if image_url and image_url.strip() and image_url.strip() not in normalized_image_urls:
        normalized_image_urls.insert(0, image_url.strip())
    primary_image_url = normalized_image_urls[0] if normalized_image_urls else None
    product = Product(
        title=title,
        slug=slug,
        description=description,
        image_url=primary_image_url,
        image_urls=normalized_image_urls,
        features=[feature.strip() for feature in features if feature.strip()],
        product_type=product_type,
        seo_title=seo_title,
        seo_description=seo_description,
        status="DRAFT",
        created_by=created_by,
    )
    db.add(product)
    await db.flush()
    for variant_payload in normalized_variants:
        variant = ProductVariant(product_id=product.id, sku=variant_payload["sku"], title=variant_payload["size"], price_minor=variant_payload["price_minor"], currency=variant_payload.get("currency", currency).upper(), status="ACTIVE")
        db.add(variant)
        await db.flush()
        if variant_payload.get("initial_stock"):
            await adjust_stock(db, variant.id, variant_payload["initial_stock"], "Initial stock with product creation")
    await db.commit()
    await db.refresh(product)
    return product


async def publish_product(db: AsyncSession, product_id) -> Product:
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if product is None:
        raise ValueError("Product not found")
    product.status = "ACTIVE"
    product.published_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(product)
    return product


async def unpublish_product(db: AsyncSession, product_id) -> Product:
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if product is None:
        raise ValueError("Product not found")
    product.status = "DRAFT"
    await db.commit()
    await db.refresh(product)
    return product


async def delete_product(db: AsyncSession, product_id) -> None:
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if product is None:
        raise ValueError("Product not found")
    await db.delete(product)
    await db.commit()


async def update_product(db: AsyncSession, product_id, **changes) -> Product:
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if product is None:
        raise ValueError("Product not found")

    slug = changes.get("slug")
    if slug and slug != product.slug:
        existing = await db.execute(select(Product).where(Product.slug == slug, Product.id != product.id))
        if existing.scalar_one_or_none() is not None:
            raise ValueError(f"A product with slug '{slug}' already exists")

    for field in ("title", "slug", "description", "product_type"):
        if changes.get(field) is not None:
            setattr(product, field, changes[field])
    if changes.get("features") is not None:
        product.features = [feature.strip() for feature in changes["features"] if feature.strip()]
    if changes.get("image_urls") is not None:
        urls = [url.strip() for url in changes["image_urls"] if url.strip()]
        product.image_urls = urls
        product.image_url = urls[0] if urls else None

    variant_result = await db.execute(select(ProductVariant).where(ProductVariant.product_id == product.id).order_by(ProductVariant.created_at.asc()))
    variant = variant_result.scalars().first()
    sku = changes.get("sku")
    if variant is not None:
        if sku and sku != variant.sku:
            existing_sku = await db.execute(select(ProductVariant).where(ProductVariant.sku == sku, ProductVariant.id != variant.id))
            if existing_sku.scalar_one_or_none() is not None:
                raise ValueError(f"A variant with SKU '{sku}' already exists")
            variant.sku = sku
        if changes.get("price_minor") is not None:
            variant.price_minor = changes["price_minor"]
        if changes.get("currency") is not None:
            variant.currency = changes["currency"].upper()

    await db.commit()
    await db.refresh(product)
    return product
