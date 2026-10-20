from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.admin import router as admin_router
from app.api.v1.admin_customers import router as admin_customers_router
from app.api.v1.admin_order_queue import router as admin_order_queue_router
from app.api.v1.auth import router as auth_router
from app.api.v1.cart import router as cart_router
from app.api.v1.checkout import router as checkout_router
from app.api.v1.customer import router as customer_router
from app.api.v1.orders import router as orders_router
from app.api.v1.payments import router as payments_router
from app.api.v1.policies import router as policies_router
from app.api.v1.public_catalogue import router as public_router
from app.api.v1.webhooks import router as webhooks_router
from app.api.v1.coupons import router as coupons_router
from app.api.v1.gst import router as gst_router


router = APIRouter()
router.include_router(public_router, tags=["public"])
router.include_router(policies_router, tags=["public"])
router.include_router(auth_router, prefix="/auth", tags=["auth"])
router.include_router(customer_router, tags=["customer"])
router.include_router(orders_router, tags=["orders"])
router.include_router(cart_router, tags=["cart"])
router.include_router(checkout_router, tags=["checkout"])
router.include_router(payments_router, prefix="/payments", tags=["payments"])
router.include_router(admin_router, prefix="/admin", tags=["admin"])
router.include_router(admin_customers_router, prefix="/admin/customers", tags=["admin"])
router.include_router(admin_order_queue_router, prefix="/admin/order-queue", tags=["admin"])
router.include_router(coupons_router, prefix="/admin/coupons", tags=["admin"])
router.include_router(gst_router, tags=["gst"])
router.include_router(webhooks_router, prefix="/webhooks", tags=["webhooks"])
