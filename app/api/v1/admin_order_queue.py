from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import AwareDatetime, BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_admin
from app.db.models import Order, OrderItem
from app.db.session import get_db

router = APIRouter(dependencies=[Depends(require_admin)])
INDIA = ZoneInfo("Asia/Kolkata")
Period = Literal["today", "7d", "30d", "all", "custom"]
Stage = Literal["all", "incoming", "PENDING", "RECEIVED", "CONFIRMED", "PROCESSING", "PACKED", "SHIPPED", "DELIVERED", "FULFILLED", "CANCELLED", "PAYMENT_FAILED"]
PaymentStatus = Literal["all", "PENDING", "UNPAID", "PAID", "FAILED", "PARTIALLY_REFUNDED", "REFUNDED"]
Fulfilment = Literal["all", "UNFULFILLED", "PARTIALLY_FULFILLED", "FULFILLED"]


class QueueOrderRead(BaseModel):
    id: UUID
    order_number: str
    status: str
    payment_status: str
    fulfilment_status: str
    currency: str
    total_minor: int
    placed_at: datetime | None
    created_at: datetime
    first_product: str | None
    item_quantity: int


class OrderQueuePage(BaseModel):
    items: list[QueueOrderRead]
    total: int
    limit: int
    offset: int
    snapshot_at: datetime
    new_orders: int
    server_today: date
    range_start: date | None
    range_end: date | None


@router.get("", response_model=OrderQueuePage)
async def order_queue(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    period: Period = "today",
    date_from: Annotated[date | None, Query(ge=date(1970, 1, 1), le=date(9998, 12, 31))] = None,
    date_to: Annotated[date | None, Query(ge=date(1970, 1, 1), le=date(9998, 12, 31))] = None,
    q: Annotated[str, Query(max_length=150)] = "",
    stage: Stage = "all",
    payment: PaymentStatus = "all",
    fulfilment: Fulfilment = "all",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    snapshot_at: AwareDatetime | None = None,
):
    now = datetime.now(timezone.utc)
    snapshot = snapshot_at or now
    if snapshot > now or snapshot < datetime(1970, 1, 1, tzinfo=timezone.utc):
        raise HTTPException(422, "Snapshot must be between 1970 and the current time")
    today = snapshot.astimezone(INDIA).date()
    start = end = None
    if period == "custom":
        if date_from is None or date_to is None or date_from > date_to:
            raise HTTPException(422, "Custom range requires valid start and end dates, with start on or before end")
        start, end = date_from, date_to
    elif date_from is not None or date_to is not None:
        raise HTTPException(422, "Dates are only accepted with a custom range")
    elif period != "all":
        start = date.fromordinal(max(1, today.toordinal() - {"today": 1, "7d": 7, "30d": 30}[period] + 1))
        end = today

    conditions = []
    placed = func.coalesce(Order.placed_at, Order.created_at)
    if start is not None and end is not None:
        conditions.extend([
            placed >= datetime.combine(start, time.min, INDIA).astimezone(timezone.utc),
            placed < datetime.combine(end + timedelta(days=1), time.min, INDIA).astimezone(timezone.utc),
        ])
    if stage != "all":
        conditions.append(Order.status.in_(["RECEIVED", "CONFIRMED"]) if stage == "incoming" else Order.status == stage)
    if payment != "all":
        conditions.append(Order.payment_status == payment)
    if fulfilment != "all":
        conditions.append(Order.fulfilment_status == fulfilment)
    search = q.strip()
    if search:
        pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        product_match = select(OrderItem.id).where(
            OrderItem.order_id == Order.id, OrderItem.product_name_snapshot.ilike(pattern, escape="\\")
        ).exists()
        conditions.append(or_(Order.order_number.ilike(pattern, escape="\\"), product_match))

    # Freeze membership against new arrivals while paging; polling reports them separately.
    total, new_orders = (await db.execute(select(
        func.count(Order.id).filter(Order.created_at <= snapshot),
        func.count(Order.id).filter(Order.created_at > snapshot),
    ).where(*conditions))).one()
    orders = (await db.execute(
        select(Order).where(*conditions, Order.created_at <= snapshot)
        .order_by(placed.desc(), Order.created_at.desc(), Order.id.desc()).limit(limit).offset(offset)
    )).scalars().all()
    summaries = {}
    if orders:
        items = (await db.execute(
            select(OrderItem.order_id, OrderItem.product_name_snapshot, OrderItem.quantity)
            .where(OrderItem.order_id.in_([order.id for order in orders]))
            .order_by(OrderItem.created_at, OrderItem.id)
        )).all()
        for order_id, product, item_quantity in items:
            first, quantity = summaries.get(order_id, (product, 0))
            summaries[order_id] = (first, quantity + item_quantity)
    response.headers["Cache-Control"] = "private, no-store"
    return OrderQueuePage(
        items=[QueueOrderRead(
            id=order.id, order_number=order.order_number, status=order.status,
            payment_status=order.payment_status, fulfilment_status=order.fulfilment_status,
            currency=order.currency, total_minor=order.total_minor, placed_at=order.placed_at,
            created_at=order.created_at, first_product=summaries.get(order.id, (None, 0))[0],
            item_quantity=summaries.get(order.id, (None, 0))[1],
        ) for order in orders],
        total=total, new_orders=new_orders, limit=limit, offset=offset, snapshot_at=snapshot,
        server_today=now.astimezone(INDIA).date(), range_start=start, range_end=end,
    )
