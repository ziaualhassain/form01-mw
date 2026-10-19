from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import select

from app.db.models import Product, ProductVariant
from app.db.session import async_session_factory
from app.services.inventory import adjust_stock


@dataclass(frozen=True)
class ProductSeed:
    kind: str
    gender: str
    theme: str
    base_price_minor: int
    features: tuple[str, ...]


SEEDS = (
    ProductSeed("shirt", "MEN", "DROP_01", 5900, ("Compact cotton", "Relaxed shoulder", "Everyday weight")),
    ProductSeed("hoodie", "UNISEX", "DROP_01", 12900, ("Heavyweight fleece", "Dropped shoulder", "Ribbed cuffs")),
    ProductSeed("sweatshirt", "WOMEN", "STUDIO", 8900, ("Brushed cotton", "Soft structure", "Clean neckline")),
    ProductSeed("oversized-shirt", "UNISEX", "OVERSIZED", 6900, ("Relaxed oversized fit", "Dropped shoulder", "Heavy cotton")),
    ProductSeed("oversized-hoodie", "UNISEX", "OVERSIZED", 14900, ("Oversized fleece", "Dropped shoulder", "Kangaroo pocket")),
    ProductSeed("oversized-sweatshirt", "UNISEX", "OVERSIZED", 9900, ("Relaxed volume", "Brushed interior", "Ribbed hem")),
)
SIZES = ("S", "M", "L", "XL")


async def seed() -> None:
    async with async_session_factory() as db:
        created = 0
        skipped = 0
        for seed_config in SEEDS:
            for number in range(1, 11):
                slug = f"{seed_config.kind}-{number:02d}"
                existing = (await db.execute(select(Product).where(Product.slug == slug))).scalar_one_or_none()
                if existing is not None:
                    skipped += 1
                    continue

                product = Product(
                    title=f"FORM/01 {seed_config.kind.title()} {number:02d}",
                    slug=slug,
                    description=f"A considered FORM/01 {seed_config.kind} for everyday motion.",
                    image_urls=[],
                    features=list(seed_config.features),
                    gender=seed_config.gender,
                    theme=seed_config.theme,
                    product_type=seed_config.kind,
                    status="ACTIVE",
                )
                db.add(product)
                await db.flush()

                for size in SIZES:
                    variant = ProductVariant(
                        product_id=product.id,
                        title=size,
                        sku=f"F01-{seed_config.kind.upper()}-{number:02d}-{size}",
                        price_minor=seed_config.base_price_minor,
                        currency="INR",
                        status="ACTIVE",
                    )
                    db.add(variant)
                    await db.flush()
                    await adjust_stock(db, variant.id, 10, "Demo catalogue seed: 10 units per size")
                created += 1

        await db.commit()
        print({"created_products": created, "skipped_existing": skipped, "variants_per_product": len(SIZES), "stock_per_variant": 10})


if __name__ == "__main__":
    asyncio.run(seed())
