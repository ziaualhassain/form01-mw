from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin
from app.db.models import CustomerProfile, Order, User
from app.db.session import get_db

def private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(dependencies=[Depends(require_admin), Depends(private_response)])


class CustomerRead(BaseModel):
    id: UUID
    email: str
    first_name: str | None
    last_name: str | None
    display_name: str | None
    phone: str | None
    status: str
    created_at: datetime
    last_login_at: datetime | None
    email_verified_at: datetime | None
    order_count: int
    paid_order_count: int
    latest_order_at: datetime | None


class CustomerPage(BaseModel):
    items: list[CustomerRead]
    total: int
    limit: int
    offset: int


class CustomerOrderRead(BaseModel):
    id: UUID
    order_number: str
    status: str
    payment_status: str
    fulfilment_status: str
    currency: str
    total_minor: int
    placed_at: datetime | None


class CustomerOrderPage(BaseModel):
    items: list[CustomerOrderRead]
    total: int
    limit: int
    offset: int


def customer_scope():
    # Staff accounts are deliberately excluded from the customer directory.
    return ~User.roles.any(name="ADMIN") & ~User.roles.any(name="OWNER")


def customer_record(user: User, profile: CustomerProfile | None, count: int, paid: int, latest: datetime | None) -> CustomerRead:
    return CustomerRead(
        id=user.id, email=user.email, status=user.status, created_at=user.created_at,
        last_login_at=user.last_login_at, email_verified_at=user.email_verified_at,
        first_name=profile.first_name if profile else None,
        last_name=profile.last_name if profile else None,
        display_name=profile.display_name if profile else None,
        phone=profile.phone if profile else None,
        order_count=count, paid_order_count=paid, latest_order_at=latest,
    )


async def customer_or_404(db: AsyncSession, customer_id: UUID) -> User:
    user = (await db.execute(select(User).where(User.id == customer_id, customer_scope()))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return user


@router.get("", response_model=CustomerPage)
async def list_customers(
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str, Query(max_length=150)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
):
    conditions = [customer_scope()]
    search = q.strip()
    if search:
        search = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{search}%"
        conditions.append(or_(*[
            column.ilike(pattern, escape="\\") for column in (
                User.email, CustomerProfile.first_name, CustomerProfile.last_name,
                CustomerProfile.display_name, CustomerProfile.phone,
                func.coalesce(CustomerProfile.first_name, "") + " " + func.coalesce(CustomerProfile.last_name, ""),
            )
        ]))
    base = select(User, CustomerProfile).outerjoin(CustomerProfile, CustomerProfile.user_id == User.id).where(*conditions)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.order_by(User.created_at.desc(), User.id.desc()).limit(limit).offset(offset))).all()
    ids = [user.id for user, _ in rows]
    statistics = {}
    if ids:
        stats = await db.execute(
            select(Order.user_id, func.count(Order.id),
                   func.count(Order.id).filter(Order.payment_status == "PAID"),
                   func.max(Order.placed_at))
            .where(Order.user_id.in_(ids)).group_by(Order.user_id)
        )
        statistics = {user_id: (count, paid, latest) for user_id, count, paid, latest in stats}
    return CustomerPage(
        items=[customer_record(user, profile, *statistics.get(user.id, (0, 0, None))) for user, profile in rows],
        total=total, limit=limit, offset=offset,
    )


@router.get("/{customer_id}", response_model=CustomerRead)
async def get_customer(customer_id: UUID, db: Annotated[AsyncSession, Depends(get_db)]):
    user = await customer_or_404(db, customer_id)
    profile = (await db.execute(select(CustomerProfile).where(CustomerProfile.user_id == user.id))).scalar_one_or_none()
    count, paid, latest = (await db.execute(
        select(func.count(Order.id), func.count(Order.id).filter(Order.payment_status == "PAID"), func.max(Order.placed_at))
        .where(Order.user_id == user.id)
    )).one()
    return customer_record(user, profile, count, paid, latest)


@router.get("/{customer_id}/orders", response_model=CustomerOrderPage)
async def customer_orders(
    customer_id: UUID, db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
):
    await customer_or_404(db, customer_id)
    total = (await db.execute(select(func.count(Order.id)).where(Order.user_id == customer_id))).scalar_one()
    orders = (await db.execute(
        select(Order).where(Order.user_id == customer_id)
        .order_by(Order.created_at.desc(), Order.id.desc()).limit(limit).offset(offset)
    )).scalars().all()
    return CustomerOrderPage(
        items=[CustomerOrderRead.model_validate(order, from_attributes=True) for order in orders],
        total=total, limit=limit, offset=offset,
    )
