from typing import Annotated
from uuid import UUID
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import Principal, require_admin
from app.db.models import Coupon
from app.db.session import get_db
from app.services.coupons import CouponRead, CouponWrite

router = APIRouter()


@router.get("", response_model=list[CouponRead])
async def list_coupons(principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(Coupon).where(Coupon.archived_at.is_(None)).order_by(Coupon.created_at.desc()))).scalars().all()


@router.post("", response_model=CouponRead, status_code=201)
async def create_coupon(payload: CouponWrite, principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    coupon = Coupon(**payload.model_dump())
    db.add(coupon)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Coupon code already exists") from exc
    await db.refresh(coupon)
    return coupon


@router.put("/{coupon_id}", response_model=CouponRead)
async def update_coupon(coupon_id: UUID, payload: CouponWrite, principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    coupon = (await db.execute(select(Coupon).where(Coupon.id == coupon_id).with_for_update())).scalar_one_or_none()
    if coupon is None or coupon.archived_at is not None:
        raise HTTPException(status_code=404, detail="Coupon not found")
    for key, value in payload.model_dump().items():
        setattr(coupon, key, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Coupon code already exists") from exc
    await db.refresh(coupon)
    return coupon


@router.delete("/{coupon_id}", status_code=204)
async def archive_coupon(coupon_id: UUID, principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    coupon = (await db.execute(select(Coupon).where(Coupon.id == coupon_id).with_for_update())).scalar_one_or_none()
    if coupon is None:
        raise HTTPException(status_code=404, detail="Coupon not found")
    if coupon.archived_at is None:
        coupon.archived_at = datetime.now(timezone.utc)
        coupon.enabled = False
        await db.commit()
