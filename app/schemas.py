from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


class Meta(BaseModel):
    request_id: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserRead(BaseModel):
    id: UUID
    email: EmailStr
    status: str
    roles: list[str] = Field(default_factory=list)
    email_verified_at: datetime | None = None


class CustomerProfileRead(UserRead):
    first_name: str | None = None
    last_name: str | None = None
    display_name: str | None = None
    phone: str | None = None
    locale: str | None = None


class CustomerProfileUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    display_name: str | None = None
    phone: str | None = None
    locale: str | None = None


class CustomerOrderSummary(BaseModel):
    id: UUID
    order_number: str
    status: str
    payment_status: str
    fulfilment_status: str
    currency: str
    total_minor: int
    placed_at: datetime | None = None


class WishlistItemRead(BaseModel):
    id: UUID
    product_id: UUID
    title: str
    slug: str
    image_url: str | None = None


class WishlistCreate(BaseModel):
    product_id: UUID


class CustomerAccountRead(CustomerProfileRead):
    addresses: list["AddressRead"] = Field(default_factory=list)
    orders: list[CustomerOrderSummary] = Field(default_factory=list)
    wishlist: list[WishlistItemRead] = Field(default_factory=list)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ProductRead(BaseModel):
    id: UUID
    title: str
    slug: str
    description: str | None = None
    image_url: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    gender: str | None = None
    theme: str | None = None
    product_type: str | None = None
    status: str
    variants: list["VariantRead"] = Field(default_factory=list)


class ProductCreate(BaseModel):
    title: str
    slug: str
    description: str | None = None
    image_url: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    gender: str | None = None
    theme: str | None = None
    product_type: str | None = None
    seo_title: str | None = None
    seo_description: str | None = None
    sku: str | None = None
    price_minor: int | None = Field(default=None, ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    initial_stock: int = Field(default=0, ge=0)
    variants: list["ProductVariantCreate"] = Field(default_factory=list)


class ProductVariantCreate(BaseModel):
    size: str
    sku: str
    price_minor: int = Field(ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    initial_stock: int = Field(default=0, ge=0)


class ProductUpdate(BaseModel):
    title: str | None = None
    slug: str | None = None
    description: str | None = None
    image_urls: list[str] | None = None
    features: list[str] | None = None
    gender: str | None = None
    theme: str | None = None
    product_type: str | None = None
    sku: str | None = None
    price_minor: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)


class VariantRead(BaseModel):
    id: UUID
    product_id: UUID
    sku: str
    title: str | None = None
    price_minor: int
    currency: str
    status: str
    on_hand: int = 0
    reserved: int = 0
    available: int = 0


class CartItemCreate(BaseModel):
    variant_id: UUID
    quantity: int = Field(default=1, ge=1, le=100)


class CartItemRead(BaseModel):
    id: UUID
    variant_id: UUID
    quantity: int
    available_quantity: int = 0


class CartRead(BaseModel):
    id: UUID
    currency: str
    items: list[CartItemRead]
    subtotal_minor: int
    total_minor: int


class AddressCreate(BaseModel):
    label: str | None = None
    recipient_name: str
    phone: str | None = None
    line1: str
    line2: str | None = None
    locality: str | None = None
    city: str
    state_or_region: str | None = None
    postal_code: str
    country_code: str = Field(min_length=2, max_length=2)
    delivery_instructions: str | None = None
    is_default_shipping: bool = False
    is_default_billing: bool = False

    @field_validator("postal_code")
    @classmethod
    def validate_postal_code(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) != 6 or not normalized.isdigit() or normalized.startswith("0"):
            raise ValueError("postal_code must be a valid 6-digit Indian PIN code")
        return normalized


class AddressRead(AddressCreate):
    id: UUID


class CheckoutQuoteRequest(BaseModel):
    shipping_address_id: UUID
    billing_address_id: UUID | None = None


class CheckoutQuoteResponse(BaseModel):
    currency: str
    subtotal_minor: int
    discount_minor: int
    shipping_minor: int
    tax_minor: int
    total_minor: int
    items: list[CartItemRead]


class CheckoutCreateRequest(CheckoutQuoteRequest):
    idempotency_key: str | None = None
    payment_provider: str | None = None


class CheckoutCreateResponse(BaseModel):
    order_id: UUID
    order_number: str
    payment_status: str
    total_minor: int
    payment_id: UUID | None = None
    payment_provider: str | None = None
    provider_order_id: str | None = None
    provider_key_id: str | None = None


class StorefrontConfig(BaseModel):
    app_name: str
    environment: str
    currency: str = "INR"


class HomepageSectionWrite(BaseModel):
    section_key: str = Field(min_length=1, max_length=120)
    section_type: str = Field(min_length=1, max_length=50)
    position: int = Field(default=0, ge=0)
    enabled: bool = True
    content: dict = Field(default_factory=dict)
    publish_at: datetime | None = None
    unpublish_at: datetime | None = None


class HomepageSectionRead(HomepageSectionWrite):
    id: UUID


class HomepageResponse(BaseModel):
    schema_version: int = 1
    page: str = "home"
    brand: dict
    announcement: dict | None = None
    sections: list[dict] = Field(default_factory=list)


class OrderItemRead(BaseModel):
    id: UUID
    product_name_snapshot: str
    variant_label_snapshot: str | None = None
    sku_snapshot: str
    product_slug_snapshot: str
    unit_price_minor: int
    quantity: int
    line_total_minor: int


class ShipmentRead(BaseModel):
    id: UUID
    carrier: str
    tracking_number: str | None = None
    tracking_url: str | None = None
    status: str
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None


class PaymentRead(BaseModel):
    id: UUID
    provider: str
    provider_order_id: str | None = None
    provider_payment_id: str | None = None
    status: str
    amount_minor: int
    currency: str


class RazorpayPaymentVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class OrderAddressRead(BaseModel):
    id: UUID
    address_type: str
    recipient_name: str
    phone: str | None = None
    line1: str
    line2: str | None = None
    locality: str | None = None
    city: str
    state_or_region: str | None = None
    postal_code: str
    country_code: str
    delivery_instructions: str | None = None


class OrderRead(BaseModel):
    id: UUID
    order_number: str
    status: str
    payment_status: str
    fulfilment_status: str
    currency: str
    subtotal_minor: int
    discount_minor: int
    shipping_minor: int
    tax_minor: int
    total_minor: int
    customer_id: UUID | None = None
    contact_email: EmailStr
    placed_at: datetime | None = None
    items: list[OrderItemRead] = Field(default_factory=list)
    payments: list[PaymentRead] = Field(default_factory=list)
    shipments: list[ShipmentRead] = Field(default_factory=list)
    shipping_address: OrderAddressRead | None = None
    billing_address: OrderAddressRead | None = None


class OrderStatusUpdateRequest(BaseModel):
    status: str = Field(min_length=2, max_length=50)
    message: str | None = Field(default=None, max_length=255)
    is_customer_visible: bool = True


class ShipmentCreateRequest(BaseModel):
    carrier: str
    tracking_number: str | None = None
    tracking_url: str | None = None
    item_quantities: dict[UUID, int] = Field(default_factory=dict)


class RefundCreateRequest(BaseModel):
    amount_minor: int | None = None
    reason: str | None = None


class InventoryAdjustRequest(BaseModel):
    variant_id: UUID
    quantity_delta: int
    note: str | None = None


class WebhookEventRequest(BaseModel):
    provider_event_id: str
    event_type: str
    payment_id: UUID | None = None
    order_number: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    payload: dict = Field(default_factory=dict)
