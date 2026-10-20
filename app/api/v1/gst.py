from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentPrincipal, Principal, require_admin
from app.core.config import get_settings
from app.db.models import GstConfiguration, Order
from app.db.session import get_db
from app.services.gst import GstDraft, GstPreview, current_gst_draft, preview_gst
from app.services.invoices import draft_invoice_pdf
from app.services.orders import get_admin_order, get_customer_order

router = APIRouter()


@router.get("/admin/gst")
async def read_gst(principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    version, configuration = await current_gst_draft(db)
    return {"version": version, "configuration": configuration.model_dump(mode="json"), "checkout_test_mode": get_settings().gst_checkout_test_mode}


@router.put("/admin/gst")
async def save_gst(payload: GstDraft, principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    record = GstConfiguration(content=payload.model_dump(mode="json"), created_by=principal.user_id)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return {"version": record.id, "configuration": record.content, "checkout_test_mode": get_settings().gst_checkout_test_mode}


@router.post("/admin/gst/preview")
async def preview(payload: GstPreview, principal: Annotated[Principal, Depends(require_admin)]):
    try:
        return preview_gst(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def invoice_response(order: Order) -> Response:
    if order.draft_invoice_snapshot is None:
        raise HTTPException(status_code=409, detail="This order predates draft invoice snapshots; no historical seller details will be invented")
    if order.payment_status not in {"PAID", "REFUNDED", "PARTIALLY_REFUNDED"}:
        raise HTTPException(status_code=409, detail="Draft document is available after payment confirmation")
    try:
        document = draft_invoice_pdf(order.draft_invoice_snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        document,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="draft-{order.id}.pdf"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/admin/orders/{order_number}/draft-invoice")
async def admin_invoice(order_number: str, principal: Annotated[Principal, Depends(require_admin)], db: AsyncSession = Depends(get_db)):
    order = await get_admin_order(db, order_number)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return await run_in_threadpool(invoice_response, order)


@router.get("/me/orders/{order_number}/draft-invoice")
async def customer_invoice(order_number: str, principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    order = await get_customer_order(db, principal.user_id, order_number)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return await run_in_threadpool(invoice_response, order)
