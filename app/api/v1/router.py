from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.admin import router as admin_router
from app.api.v1.auth import router as auth_router
from app.api.v1.cart import router as cart_router
from app.api.v1.checkout import router as checkout_router
from app.api.v1.customer import router as customer_router
from app.api.v1.orders import router as orders_router
from app.api.v1.payments import router as payments_router
from app.api.v1.public_catalogue import router as public_router
from app.api.v1.webhooks import router as webhooks_router


router = APIRouter()
router.include_router(public_router, tags=["public"])
router.include_router(auth_router, prefix="/auth", tags=["auth"])
router.include_router(customer_router, tags=["customer"])
router.include_router(orders_router, tags=["orders"])
router.include_router(cart_router, tags=["cart"])
router.include_router(checkout_router, tags=["checkout"])
router.include_router(payments_router, prefix="/payments", tags=["payments"])
router.include_router(admin_router, prefix="/admin", tags=["admin"])
router.include_router(webhooks_router, prefix="/webhooks", tags=["webhooks"])
