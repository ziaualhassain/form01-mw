from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import HomepageSection, Product
from app.services.inventory import list_variant_inventory


ALLOWED_SECTION_TYPES = {"hero", "editorial", "product_collection", "statement", "announcement", "category_grid"}


def _validate_href(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not (value.startswith("/") or value.startswith("https://form01.com")):
        raise ValueError("CTA href must be an internal route or an approved FORM/01 URL")


def _validate_content(content: dict) -> None:
    cta = content.get("cta")
    if isinstance(cta, dict):
        _validate_href(cta.get("href"))
    for key in ("desktop_url", "mobile_url"):
        image = content.get("image")
        if isinstance(image, dict) and image.get(key) is not None and not str(image[key]).startswith(("https://", "/")):
            raise ValueError(f"{key} must be an HTTPS or internal asset URL")


async def replace_homepage_sections(db: AsyncSession, sections: list[dict], created_by) -> list[HomepageSection]:
    for payload in sections:
        if payload["section_type"] not in ALLOWED_SECTION_TYPES:
            raise ValueError(f"Unsupported homepage section type: {payload['section_type']}")
        _validate_content(payload.get("content", {}))
        if payload["section_type"] == "category_grid":
            items = payload.get("content", {}).get("items")
            if not isinstance(items, list):
                raise ValueError("Category grid content.items must be a list")
            seen = set()
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("product_type"), str) or not item["product_type"].strip():
                    raise ValueError("Each category requires a non-empty product_type")
                key = item["product_type"].strip().upper()
                if key in seen:
                    raise ValueError("Category product_type must be unique")
                seen.add(key)
                image = item.get("image", {})
                if not isinstance(image, dict):
                    raise ValueError("Category image must be an object")
                for field in ("desktop_url", "mobile_url"):
                    url = image.get(field)
                    if url is not None and (not isinstance(url, str) or not (url.startswith("https://") or (url.startswith("/") and not url.startswith("//")))):
                        raise ValueError("Category image URLs must be HTTPS or internal asset paths")
    await db.execute(delete(HomepageSection))
    records = [HomepageSection(section_key=item["section_key"], section_type=item["section_type"], position=item["position"], enabled=item["enabled"], content_json=item.get("content", {}), publish_at=item.get("publish_at"), unpublish_at=item.get("unpublish_at"), created_by=created_by) for item in sections]
    db.add_all(records)
    await db.commit()
    return records


async def _product_cards(db: AsyncSession, content: dict) -> list[dict]:
    slugs = content.get("product_slugs") or []
    limit = min(int(content.get("product_limit", 4)), 12)
    query = select(Product).where(Product.status == "ACTIVE")
    if content.get("gender"):
        query = query.where(Product.gender == content["gender"])
    if content.get("theme"):
        query = query.where(Product.theme == content["theme"])
    if content.get("product_type"):
        query = query.where(Product.product_type == content["product_type"])
    if slugs:
        query = query.where(Product.slug.in_(slugs))
    query = query.order_by(Product.created_at.desc()).limit(limit)
    products = list((await db.execute(query)).scalars().all())
    inventory = await list_variant_inventory(db, [product.id for product in products])
    return [{"id": product.id, "title": product.title, "slug": product.slug, "description": product.description, "image_url": product.image_url, "image_urls": product.image_urls, "features": product.features, "gender": product.gender, "theme": product.theme, "product_type": product.product_type, "variants": inventory.get(product.id, [])} for product in products]


async def get_public_homepage(db: AsyncSession) -> dict:
    now = datetime.now(timezone.utc)
    result = await db.execute(select(HomepageSection).where(HomepageSection.enabled.is_(True), (HomepageSection.publish_at.is_(None) | (HomepageSection.publish_at <= now)), (HomepageSection.unpublish_at.is_(None) | (HomepageSection.unpublish_at > now))).order_by(HomepageSection.position.asc(), HomepageSection.created_at.asc()))
    sections = []
    for section in result.scalars().all():
        content = dict(section.content_json or {})
        resolved = {"id": section.id, "type": section.section_type, "position": section.position, "content": content}
        if section.section_type == "product_collection":
            resolved["products"] = await _product_cards(db, content)
        sections.append(resolved)
    return {"schema_version": 1, "page": "home", "brand": {"name": "FORM/01", "tagline": "Built beyond trends."}, "announcement": next((section["content"] for section in sections if section["type"] == "announcement"), None), "sections": sections}


async def get_admin_homepage(db: AsyncSession) -> list[HomepageSection]:
    result = await db.execute(select(HomepageSection).order_by(HomepageSection.position.asc(), HomepageSection.created_at.asc()))
    return list(result.scalars().all())
