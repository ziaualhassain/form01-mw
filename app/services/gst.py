from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Address, CartItem, GstConfiguration, Product, ProductVariant
from collections.abc import Sequence
from sqlalchemy.engine import Row


# Union territories and special supply cases are deliberately unsupported in this test model.
GST_TEST_STATES = {
    "02": "Himachal Pradesh", "03": "Punjab",
    "05": "Uttarakhand", "06": "Haryana", "08": "Rajasthan",
    "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura",
    "17": "Meghalaya", "18": "Assam", "19": "West Bengal", "20": "Jharkhand",
    "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "27": "Maharashtra", "29": "Karnataka", "30": "Goa", "32": "Kerala",
    "33": "Tamil Nadu", "36": "Telangana", "37": "Andhra Pradesh",
}


class DraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GstSlab(DraftModel):
    max_unit_price_minor: int | None = Field(default=None, gt=0, le=2_000_000_000)
    rate_basis_points: int = Field(ge=0, le=10000)


class GstRule(DraftModel):
    product_type: str = Field(min_length=1, max_length=100)
    hsn: str = Field(pattern=r"^\d{4}(\d{2})?(\d{2})?$")
    effective_from: date
    effective_until: date | None = None
    slab_basis: Literal["BEFORE_DISCOUNT", "AFTER_DISCOUNT"] = "BEFORE_DISCOUNT"
    slabs: list[GstSlab] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_rule(self) -> Self:
        self.product_type = self.product_type.strip().upper()
        if not self.product_type:
            raise ValueError("Product type cannot be blank")
        if self.effective_until and self.effective_until <= self.effective_from:
            raise ValueError("Effective end must be after effective start")
        previous = 0
        for index, slab in enumerate(self.slabs):
            if slab.max_unit_price_minor is None:
                if index != len(self.slabs) - 1:
                    raise ValueError("Only the final slab can have no upper bound")
            elif slab.max_unit_price_minor <= previous:
                raise ValueError("Slab upper bounds must increase strictly")
            else:
                previous = slab.max_unit_price_minor
        if self.slabs[-1].max_unit_price_minor is not None:
            raise ValueError("Final slab must have no upper bound")
        return self


class GstDraft(DraftModel):
    draft_only: Literal[True] = True
    registration: Literal["UNKNOWN"] = "UNKNOWN"
    pricing: Literal["EXCLUSIVE"] = "EXCLUSIVE"
    legal_name: str = Field(default="Shaik Ziaual Hassain", max_length=255)
    address: str = Field(default="", max_length=2000)
    gstin: str = Field(default="", max_length=15, pattern=r"^$|^[0-9A-Z]{15}$")
    seller_state_code: str = Field(default="36", pattern=r"^\d{2}$")
    source_url: str = Field(default="https://razorpay.com/learn/gst-on-clothes/", max_length=500)
    notes: str = Field(default="Registration, rates and invoice numbering require accountant approval.", max_length=4000)
    rules: list[GstRule] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_windows(self) -> Self:
        for index, rule in enumerate(self.rules):
            for other in self.rules[:index]:
                if rule.product_type == other.product_type and (
                    other.effective_until is None or rule.effective_from < other.effective_until
                ) and (rule.effective_until is None or other.effective_from < rule.effective_until):
                    raise ValueError(f"Effective windows overlap for {rule.product_type}")
        return self


class GstLine(DraftModel):
    product_type: str = Field(min_length=1, max_length=100)
    unit_price_minor: int = Field(gt=0, le=20_000_000)
    quantity: int = Field(ge=1, le=100)


class GstPreview(DraftModel):
    configuration: GstDraft
    destination_state_code: str = Field(pattern=r"^\d{2}$")
    on_date: date
    discount_minor: int = Field(default=0, ge=0, le=2_000_000_000)
    lines: list[GstLine] = Field(min_length=1, max_length=100)


async def current_gst_draft(db: AsyncSession) -> tuple[int, GstDraft]:
    latest = (await db.execute(select(GstConfiguration).order_by(GstConfiguration.id.desc()).limit(1))).scalar_one_or_none()
    return (latest.id, GstDraft.model_validate(latest.content)) if latest else (0, GstDraft())


def preview_gst(payload: GstPreview) -> dict:
    """Draft domestic single-registration goods simulation, never checkout authority."""
    subtotals = [line.unit_price_minor * line.quantity for line in payload.lines]
    subtotal = sum(subtotals)
    if payload.discount_minor > subtotal:
        raise ValueError("Discount cannot exceed subtotal")
    discounts = [amount * payload.discount_minor // subtotal for amount in subtotals]
    remainder = payload.discount_minor - sum(discounts)
    # Largest remainders allocate every paise once, with stable line-order ties.
    ranked = sorted(range(len(subtotals)), key=lambda i: -(subtotals[i] * payload.discount_minor % subtotal))
    for index in ranked[:remainder]:
        discounts[index] += 1
    interstate = payload.configuration.seller_state_code != payload.destination_state_code
    lines = []
    for line, amount, discount in zip(payload.lines, subtotals, discounts):
        rule = next((rule for rule in payload.configuration.rules
                     if rule.product_type == line.product_type.strip().upper()
                     and rule.effective_from <= payload.on_date
                     and (rule.effective_until is None or payload.on_date < rule.effective_until)), None)
        if rule is None:
            raise ValueError(f"No draft rule effective for product type {line.product_type}")
        taxable = amount - discount
        basis = amount if rule.slab_basis == "BEFORE_DISCOUNT" else taxable
        slab = next(slab for slab in rule.slabs if slab.max_unit_price_minor is None or basis <= slab.max_unit_price_minor * line.quantity)
        tax = (taxable * slab.rate_basis_points + 5000) // 10000
        cgst = 0 if interstate else tax // 2
        lines.append({
            "product_type": rule.product_type, "hsn": rule.hsn, "quantity": line.quantity,
            "subtotal_minor": amount, "discount_minor": discount, "taxable_minor": taxable,
            "rate_basis_points": slab.rate_basis_points, "tax_minor": tax,
            "cgst_minor": cgst, "sgst_minor": 0 if interstate else tax - cgst,
            "igst_minor": tax if interstate else 0,
        })
    tax = sum(line["tax_minor"] for line in lines)
    return {
        "draft_only": True, "notice": "Simulation only. Not charged at checkout. Not a tax invoice.",
        "subtotal_minor": subtotal, "discount_minor": payload.discount_minor,
        "taxable_minor": subtotal - payload.discount_minor, "tax_minor": tax,
        "total_minor": subtotal - payload.discount_minor + tax,
        "cgst_minor": sum(line["cgst_minor"] for line in lines),
        "sgst_minor": sum(line["sgst_minor"] for line in lines),
        "igst_minor": sum(line["igst_minor"] for line in lines), "lines": lines,
    }


async def checkout_test_gst(
    db: AsyncSession, rows: Sequence[Row[tuple[CartItem, ProductVariant, Product]]],
    address: Address, discount_minor: int,
    configuration: tuple[int, GstDraft] | None = None,
) -> dict:
    version, config = configuration if configuration is not None else await current_gst_draft(db)
    if version == 0:
        raise ValueError("Save draft GST product rules in admin before testing checkout")
    if address.country_code.strip().upper() != "IN":
        raise ValueError("GST test checkout supports Indian delivery addresses only")
    state = (address.state_or_region or "").strip().casefold()
    destination = next((code for code, name in GST_TEST_STATES.items() if state in {code, name.casefold()}), None)
    if destination is None or config.seller_state_code not in GST_TEST_STATES:
        raise ValueError("GST test checkout requires a supported full Indian state name or two-digit state code; union territories and special supplies are not supported")
    if not rows:
        raise ValueError("Cart is empty")
    lines: list[GstLine] = []
    for item, variant, product in rows:
        if not product.product_type:
            raise ValueError(f"Set a product type for {product.title} in admin before testing GST")
        if variant.currency != "INR":
            raise ValueError("GST test checkout supports INR products only")
        lines.append(GstLine(product_type=product.product_type, unit_price_minor=variant.price_minor, quantity=item.quantity))
    on_date = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    result = preview_gst(GstPreview(
        configuration=config, destination_state_code=destination, on_date=on_date,
        discount_minor=discount_minor,
        lines=lines,
    ))
    for line, (item, variant, _) in zip(result["lines"], rows):
        line.update(cart_item_id=str(item.id), variant_id=str(variant.id), sku=variant.sku)
    return {
        **result, "configuration_version": version, "destination_state_code": destination,
        "seller_state_code": config.seller_state_code, "on_date": on_date.isoformat(),
        "notice": "LOCAL GST TEST ONLY. Draft rates included in Razorpay Test amount. No real GST collected. Not a tax invoice.",
    }
