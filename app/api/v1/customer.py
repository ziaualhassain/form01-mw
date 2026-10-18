from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentPrincipal
from app.db.models import Address, CustomerProfile, Order, Product, User, WishlistItem
from app.db.session import get_db
from app.schemas import AddressCreate, AddressRead, CustomerAccountRead, CustomerOrderSummary, CustomerProfileRead, CustomerProfileUpdate, UserRead, WishlistCreate, WishlistItemRead


router = APIRouter()


@router.get("/me/profile", response_model=CustomerAccountRead)
async def profile(principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User, CustomerProfile).outerjoin(CustomerProfile, CustomerProfile.user_id == User.id).where(User.id == principal.user_id))
    row = result.one_or_none()
    user, profile = row if row else (None, None)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    addresses = (await db.execute(select(Address).where(Address.user_id == user.id).order_by(Address.created_at.desc()))).scalars().all()
    orders = (await db.execute(select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc()))).scalars().all()
    wishlist_rows = (await db.execute(select(WishlistItem, Product).join(Product, Product.id == WishlistItem.product_id).where(WishlistItem.user_id == user.id).order_by(WishlistItem.created_at.desc()))).all()
    return CustomerAccountRead(
        id=user.id,
        email=user.email,
        status=user.status,
        roles=principal.roles,
        email_verified_at=user.email_verified_at,
        first_name=profile.first_name if profile else None,
        last_name=profile.last_name if profile else None,
        display_name=profile.display_name if profile else None,
        phone=profile.phone if profile else None,
        locale=profile.locale if profile else None,
        addresses=[AddressRead(id=item.id, label=item.label, recipient_name=item.recipient_name, phone=item.phone, line1=item.line1, line2=item.line2, locality=item.locality, city=item.city, state_or_region=item.state_or_region, postal_code=item.postal_code, country_code=item.country_code, delivery_instructions=item.delivery_instructions, is_default_shipping=item.is_default_shipping, is_default_billing=item.is_default_billing) for item in addresses],
        orders=[CustomerOrderSummary(id=order.id, order_number=order.order_number, status=order.status, payment_status=order.payment_status, fulfilment_status=order.fulfilment_status, currency=order.currency, total_minor=order.total_minor, placed_at=order.placed_at) for order in orders],
        wishlist=[WishlistItemRead(id=item.id, product_id=product.id, title=product.title, slug=product.slug, image_url=product.image_url) for item, product in wishlist_rows],
    )


@router.patch("/me/profile", response_model=CustomerProfileRead)
async def update_profile(payload: CustomerProfileUpdate, principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    profile_result = await db.execute(select(CustomerProfile).where(CustomerProfile.user_id == principal.user_id))
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        profile = CustomerProfile(user_id=principal.user_id)
        db.add(profile)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    await db.commit()
    await db.refresh(profile)
    return CustomerProfileRead(id=principal.user_id, email=principal.email, status="ACTIVE", roles=principal.roles, first_name=profile.first_name, last_name=profile.last_name, display_name=profile.display_name, phone=profile.phone, locale=profile.locale)


@router.get("/me/wishlist", response_model=list[WishlistItemRead])
async def wishlist(principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(WishlistItem, Product).join(Product, Product.id == WishlistItem.product_id).where(WishlistItem.user_id == principal.user_id).order_by(WishlistItem.created_at.desc()))).all()
    return [WishlistItemRead(id=item.id, product_id=product.id, title=product.title, slug=product.slug, image_url=product.image_url) for item, product in rows]


@router.post("/me/wishlist", response_model=WishlistItemRead, status_code=201)
async def add_wishlist(payload: WishlistCreate, principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    product = (await db.execute(select(Product).where(Product.id == payload.product_id))).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    existing = (await db.execute(select(WishlistItem).where(WishlistItem.user_id == principal.user_id, WishlistItem.product_id == product.id))).scalar_one_or_none()
    if existing is not None:
        return WishlistItemRead(id=existing.id, product_id=product.id, title=product.title, slug=product.slug, image_url=product.image_url)
    item = WishlistItem(user_id=principal.user_id, product_id=product.id)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return WishlistItemRead(id=item.id, product_id=product.id, title=product.title, slug=product.slug, image_url=product.image_url)


@router.delete("/me/wishlist/{product_id}", status_code=204)
async def remove_wishlist(product_id: str, principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    item = (await db.execute(select(WishlistItem).where(WishlistItem.user_id == principal.user_id, WishlistItem.product_id == product_id))).scalar_one_or_none()
    if item is not None:
        await db.delete(item)
        await db.commit()


@router.get("/me/addresses", response_model=list[AddressRead])
async def list_addresses(principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Address).where(Address.user_id == principal.user_id).order_by(Address.created_at.desc()))
    return [AddressRead(id=item.id, label=item.label, recipient_name=item.recipient_name, phone=item.phone, line1=item.line1, line2=item.line2, locality=item.locality, city=item.city, state_or_region=item.state_or_region, postal_code=item.postal_code, country_code=item.country_code, delivery_instructions=item.delivery_instructions, is_default_shipping=item.is_default_shipping, is_default_billing=item.is_default_billing) for item in result.scalars().all()]


@router.post("/me/addresses", response_model=AddressRead, status_code=201)
async def create_address(payload: AddressCreate, principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    address = Address(user_id=principal.user_id, **payload.model_dump())
    db.add(address)
    await db.commit()
    await db.refresh(address)
    return AddressRead(id=address.id, **payload.model_dump())


@router.delete("/me/addresses/{address_id}", status_code=204)
async def delete_address(address_id: str, principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Address).where(Address.id == address_id, Address.user_id == principal.user_id))
    address = result.scalar_one_or_none()
    if address is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Address not found")
    await db.delete(address)
    await db.commit()
