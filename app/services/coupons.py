from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Coupon, CouponRedemption


class CouponWrite(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    discount_type: str
    value: int = Field(gt=0, le=2_000_000_000)
    max_discount_minor: int | None = Field(default=None, gt=0, le=2_000_000_000)
    min_subtotal_minor: int = Field(default=0, ge=0, le=2_000_000_000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    usage_limit: int | None = Field(default=None, gt=0, le=2_000_000_000)
    per_customer_limit: int | None = Field(default=None, gt=0, le=2_000_000_000)
    enabled: bool = True

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        code = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9_-]{1,40}", code):
            raise ValueError("Code must contain only letters, numbers, hyphens or underscores")
        return code

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Coupon dates must include a timezone")
        return value.astimezone(timezone.utc) if value is not None else None

    @model_validator(mode="after")
    def validate_rules(self):
        if self.discount_type not in {"PERCENTAGE", "FIXED"}:
            raise ValueError("Discount type must be PERCENTAGE or FIXED")
        if self.discount_type == "PERCENTAGE" and self.value > 100:
            raise ValueError("Percentage must be between 1 and 100")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("End date must be after start date")
        return self


class CouponRead(CouponWrite):
    model_config = ConfigDict(from_attributes=True)
    id: UUID

    @field_validator("starts_at", "ends_at", mode="before")
    @classmethod
    def restore_database_timezone(cls, value: datetime | None) -> datetime | None:
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


def discount_amount(coupon: Coupon, subtotal: int, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    def utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    if coupon.archived_at is not None:
        raise ValueError("This coupon has been archived")
    if not coupon.enabled:
        raise ValueError("This coupon is disabled")
    if coupon.starts_at and utc(coupon.starts_at) > now:
        raise ValueError("This coupon is not active yet")
    if coupon.ends_at and utc(coupon.ends_at) <= now:
        raise ValueError("This coupon has expired")
    if subtotal < coupon.min_subtotal_minor:
        raise ValueError(f"This coupon requires a subtotal of at least INR {coupon.min_subtotal_minor / 100:.2f}")
    amount = subtotal * coupon.value // 100 if coupon.discount_type == "PERCENTAGE" else coupon.value
    if coupon.max_discount_minor is not None:
        amount = min(amount, coupon.max_discount_minor)
    # Razorpay checkout must retain at least INR 1 payable.
    amount = min(amount, max(0, subtotal - 100))
    if amount <= 0:
        raise ValueError("This coupon cannot discount this cart; at least INR 1 must remain payable")
    return amount


async def validate_coupon(db: AsyncSession, code: str, user_id, subtotal: int, *, reserve: bool = False) -> tuple[Coupon, int]:
    query = select(Coupon).where(Coupon.code == code.strip().upper())
    if reserve:
        query = query.with_for_update()
    coupon = (await db.execute(query)).scalar_one_or_none()
    if coupon is None:
        raise ValueError("Coupon code is invalid")
    amount = discount_amount(coupon, subtotal)
    active = (CouponRedemption.coupon_id == coupon.id, CouponRedemption.status.in_(["RESERVED", "REDEEMED"]))
    if coupon.usage_limit is not None:
        count = await db.scalar(select(func.count()).select_from(CouponRedemption).where(*active))
        if count >= coupon.usage_limit:
            raise ValueError("This coupon's usage limit has been reached")
    if coupon.per_customer_limit is not None:
        count = await db.scalar(select(func.count()).select_from(CouponRedemption).where(*active, CouponRedemption.user_id == user_id))
        if count >= coupon.per_customer_limit:
            raise ValueError("You have reached this coupon's usage limit")
    return coupon, amount


async def finish_redemption(db: AsyncSession, order_id, status: str) -> None:
    redemption = (await db.execute(select(CouponRedemption).where(CouponRedemption.order_id == order_id))).scalar_one_or_none()
    if redemption is not None:
        await db.execute(select(Coupon).where(Coupon.id == redemption.coupon_id).with_for_update())
        if redemption.status == "RESERVED":
            redemption.status = status
