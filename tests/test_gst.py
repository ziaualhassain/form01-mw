import copy
import base64
import re
import unittest
import zlib
from datetime import date
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.dependencies import Principal, get_current_principal
from app.api.v1.gst import router
from app.db.base import Base
from app.db.models import Address, Cart, CartItem, GstConfiguration, InventoryLevel, InventoryLocation, Product, ProductVariant, User
from app.db.session import get_db
from app.services.checkout import place_order, quote_cart
from app.services.gst import GstDraft, GstPreview, current_gst_draft, preview_gst
from app.services.invoices import draft_invoice_pdf
from app.services.orders import mark_payment_captured, serialize_order


def configuration():
    return GstDraft(rules=[{
        "product_type": "shirt", "hsn": "6109", "effective_from": "2026-01-01",
        "effective_until": "2027-01-01", "slab_basis": "BEFORE_DISCOUNT",
        "slabs": [{"max_unit_price_minor": 100000, "rate_basis_points": 500},
                  {"max_unit_price_minor": None, "rate_basis_points": 1200}],
    }])


class GstCalculationTests(unittest.TestCase):
    def calculate(self, price=100000, quantity=1, discount=0, state="36", config=None, on_date="2026-05-01", lines=None):
        return preview_gst(GstPreview(
            configuration=config or configuration(), destination_state_code=state,
            on_date=date.fromisoformat(on_date), discount_minor=discount,
            lines=lines or [{"product_type": "SHIRT", "unit_price_minor": price, "quantity": quantity}],
        ))

    def test_default_is_disabled_without_seeded_rates(self):
        draft = GstDraft()
        self.assertEqual((draft.registration, draft.pricing, draft.seller_state_code, draft.rules), ("UNKNOWN", "EXCLUSIVE", "36", []))
        self.assertTrue(draft.draft_only)
        for update in [{"draft_only": False}, {"registration": "REGULAR"}, {"pricing": "INCLUSIVE"}, {"collect_tax": True}]:
            with self.subTest(update=update), self.assertRaises(ValidationError):
                GstDraft(**update)

    def test_exact_slab_boundaries_are_per_unit_not_line_total(self):
        self.assertEqual(self.calculate(price=99999)["tax_minor"], 5000)
        self.assertEqual(self.calculate(price=100000, quantity=2)["tax_minor"], 10000)
        self.assertEqual(self.calculate(price=100001)["tax_minor"], 12000)

    def test_same_state_and_interstate_components_reconcile(self):
        local = self.calculate()
        interstate = self.calculate(state="29")
        self.assertEqual((local["cgst_minor"], local["sgst_minor"], local["igst_minor"]), (2500, 2500, 0))
        self.assertEqual((interstate["cgst_minor"], interstate["sgst_minor"], interstate["igst_minor"]), (0, 0, 5000))
        self.assertEqual(local["total_minor"], 105000)
        odd = self.calculate(price=10)
        self.assertEqual((odd["tax_minor"], odd["cgst_minor"], odd["sgst_minor"]), (1, 0, 1))

    def test_discount_changes_taxable_value_and_configured_slab_basis(self):
        before = self.calculate(price=100100, discount=100)
        config = configuration()
        config.rules[0].slab_basis = "AFTER_DISCOUNT"
        after = self.calculate(price=100100, discount=100, config=config)
        self.assertEqual((before["taxable_minor"], before["tax_minor"]), (100000, 12000))
        self.assertEqual(after["tax_minor"], 5000)
        self.assertEqual(after["total_minor"], 105000)

    def test_discount_allocation_preserves_every_paise_and_stable_ties(self):
        result = self.calculate(discount=2, lines=[
            {"product_type": "SHIRT", "unit_price_minor": 101, "quantity": 1},
            {"product_type": "SHIRT", "unit_price_minor": 101, "quantity": 1},
            {"product_type": "SHIRT", "unit_price_minor": 101, "quantity": 1},
        ])
        self.assertEqual([line["discount_minor"] for line in result["lines"]], [1, 1, 0])
        self.assertEqual(sum(line["taxable_minor"] for line in result["lines"]), 301)
        self.assertEqual(result["total_minor"], result["subtotal_minor"] - 2 + result["tax_minor"])
        with self.assertRaisesRegex(ValueError, "exceed"):
            self.calculate(price=1, discount=2)
        self.assertEqual(self.calculate(price=100, discount=100)["tax_minor"], 0)

    def test_effective_dates_and_missing_classification_fail_explicitly(self):
        self.assertEqual(self.calculate(on_date="2026-01-01")["tax_minor"], 5000)
        for when in ["2025-12-31", "2027-01-01"]:
            with self.subTest(when=when), self.assertRaisesRegex(ValueError, "No draft rule"):
                self.calculate(on_date=when)
        with self.assertRaisesRegex(ValueError, "No draft rule"):
            self.calculate(config=GstDraft())

    def test_invalid_slabs_overlaps_hsn_and_rates_rejected(self):
        base = configuration().model_dump(mode="json")
        invalid = []
        for slabs in [
            [{"max_unit_price_minor": 100, "rate_basis_points": 500}],
            [{"max_unit_price_minor": None, "rate_basis_points": 500}, {"max_unit_price_minor": None, "rate_basis_points": 1200}],
            [{"max_unit_price_minor": 100, "rate_basis_points": 500}, {"max_unit_price_minor": 99, "rate_basis_points": 1200}, {"rate_basis_points": 1200}],
            [{"rate_basis_points": -1}], [{"rate_basis_points": 10001}],
        ]:
            payload = copy.deepcopy(base)
            payload["rules"][0]["slabs"] = slabs
            invalid.append(payload)
        for changes in [{"hsn": "abc"}, {"product_type": "   "}, {"effective_until": "2025-12-31"}]:
            payload = copy.deepcopy(base)
            payload["rules"][0].update(changes)
            invalid.append(payload)
        duplicate = copy.deepcopy(base)
        duplicate["rules"].append(copy.deepcopy(duplicate["rules"][0]))
        invalid.append(duplicate)
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                GstDraft.model_validate(payload)
        next_window = copy.deepcopy(base)
        rule = copy.deepcopy(next_window["rules"][0])
        rule.update(effective_from="2027-01-01", effective_until=None)
        next_window["rules"].append(rule)
        self.assertEqual(len(GstDraft.model_validate(next_window).rules), 2)


class GstDocumentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.db = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.user = User(email="draft@example.com", password_hash="fixture")
        self.product = Product(title="Shirt <b>& literal markup</b>", slug="shirt", status="ACTIVE")
        self.location = InventoryLocation(code="TEST", name="Test")
        self.db.add_all([self.user, self.product, self.location])
        await self.db.flush()
        self.variant = ProductVariant(product_id=self.product.id, sku="DRAFT-1", price_minor=100000)
        self.cart = Cart(user_id=self.user.id)
        self.address = Address(user_id=self.user.id, recipient_name="Draft <Customer>", line1="Test street", city="Hyderabad", postal_code="500001", country_code="IN")
        self.db.add_all([self.variant, self.cart, self.address])
        await self.db.flush()
        self.db.add_all([
            CartItem(cart_id=self.cart.id, variant_id=self.variant.id, quantity=2),
            InventoryLevel(location_id=self.location.id, variant_id=self.variant.id, on_hand=10, reserved=0),
        ])
        await self.db.commit()
        self.principal = Principal(user_id=self.user.id, email=self.user.email, roles=["ADMIN"])
        self.app = FastAPI()
        self.app.include_router(router)
        async def session():
            yield self.db
        self.app.dependency_overrides[get_db] = session
        self.app.dependency_overrides[get_current_principal] = lambda: self.principal
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.db.close()
        await self.engine.dispose()

    async def checkout(self):
        with patch("app.services.checkout.create_razorpay_order", return_value={"id": "order_draft"}) as provider:
            order, payment = await place_order(
                self.db, self.cart.id, self.user.email, user_id=self.user.id,
                shipping_address_id=self.address.id, payment_provider="razorpay", idempotency_key=str(uuid4()),
            )
        self.assertEqual(provider.call_args.kwargs["amount_minor"], 200000)
        return order, payment

    async def test_versioned_settings_do_not_charge_draft_tax_or_change_old_snapshots(self):
        version, default = await current_gst_draft(self.db)
        self.assertEqual((version, default.rules), (0, []))
        saved = await self.client.put("/admin/gst", json=configuration().model_dump(mode="json"))
        self.assertEqual(saved.status_code, 200)
        version = saved.json()["version"]
        quote = await quote_cart(self.db, self.cart.id, self.user.id)
        order, payment = await self.checkout()
        self.assertEqual((quote["tax_minor"], order.tax_minor, order.total_minor, payment.amount_minor), (0, 0, 200000, 200000))
        frozen = copy.deepcopy(order.draft_invoice_snapshot)
        self.assertEqual(frozen["configuration_version"], version)
        updated = configuration().model_dump(mode="json")
        updated["legal_name"] = "New seller draft"
        response = await self.client.put("/admin/gst", json=updated)
        self.assertGreater(response.json()["version"], version)
        records = (await self.db.execute(select(GstConfiguration).order_by(GstConfiguration.id))).scalars().all()
        self.assertEqual(len(records), 2)
        self.assertNotEqual(records[0].content["legal_name"], records[1].content["legal_name"])
        await self.db.refresh(order)
        self.assertEqual(order.draft_invoice_snapshot, frozen)
        latest = await self.client.get("/admin/gst")
        self.assertEqual(latest.json()["configuration"]["legal_name"], "New seller draft")
        preview = await self.client.post("/admin/gst/preview", json={
            "configuration": updated, "destination_state_code": "36", "on_date": "2026-05-01",
            "lines": [{"product_type": "SHIRT", "unit_price_minor": 100000, "quantity": 2}],
        })
        self.assertEqual(preview.json()["tax_minor"], 10000)

    async def test_pdf_auth_ownership_payment_gate_and_missing_legacy_snapshot(self):
        order, payment = await self.checkout()
        path = f"/me/orders/{order.order_number}/draft-invoice"
        self.assertEqual((await self.client.get(path)).status_code, 409)
        self.assertFalse((await serialize_order(self.db, order))["draft_invoice_available"])
        await mark_payment_captured(self.db, payment)
        self.assertTrue((await serialize_order(self.db, order))["draft_invoice_available"])
        customer = await self.client.get(path)
        admin = await self.client.get(f"/admin/orders/{order.order_number}/draft-invoice")
        for response in [customer, admin]:
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.content.startswith(b"%PDF-"))
            self.assertTrue(response.content.rstrip().endswith(b"%%EOF"))
            self.assertEqual(response.headers["content-type"], "application/pdf")
            self.assertEqual(response.headers["cache-control"], "private, no-store")
            self.assertIn(".pdf", response.headers["content-disposition"])
        self.principal = Principal(user_id=uuid4(), email="other@example.com", roles=["CUSTOMER"])
        self.assertEqual((await self.client.get(path)).status_code, 404)
        self.assertEqual((await self.client.get("/admin/gst")).status_code, 403)
        self.assertEqual((await self.client.put("/admin/gst", json={})).status_code, 403)
        self.assertEqual((await self.client.post("/admin/gst/preview", json={})).status_code, 403)
        self.assertEqual((await self.client.get(f"/admin/orders/{order.order_number}/draft-invoice")).status_code, 403)
        self.principal = Principal(user_id=self.user.id, email=self.user.email, roles=["CUSTOMER"])
        order.payment_status = "REFUNDED"
        await self.db.commit()
        self.assertEqual((await self.client.get(path)).status_code, 200)
        order.draft_invoice_snapshot = None
        await self.db.commit()
        self.assertEqual((await self.client.get(path)).status_code, 409)
        self.assertEqual((await self.client.get("/me/orders/unknown/draft-invoice")).status_code, 404)
        del self.app.dependency_overrides[get_current_principal]
        for method, route in [
            ("GET", "/admin/gst"), ("PUT", "/admin/gst"), ("POST", "/admin/gst/preview"),
            ("GET", path), ("GET", f"/admin/orders/{order.order_number}/draft-invoice"),
        ]:
            self.assertEqual((await self.client.request(method, route)).status_code, 401)

    async def test_invalid_preview_and_activation_rejected_over_http(self):
        invalid = configuration().model_dump(mode="json")
        invalid["draft_only"] = False
        self.assertEqual((await self.client.put("/admin/gst", json=invalid)).status_code, 422)
        payload = {
            "configuration": GstDraft().model_dump(mode="json"), "destination_state_code": "36",
            "on_date": "2026-05-01", "lines": [{"product_type": "SHIRT", "unit_price_minor": 100, "quantity": 1}],
        }
        response = await self.client.post("/admin/gst/preview", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("No draft rule", response.json()["detail"])

    async def test_pdf_multipage_escaping_totals_and_explicit_font_failure(self):
        order, payment = await self.checkout()
        snapshot = copy.deepcopy(order.draft_invoice_snapshot)
        snapshot["lines"] *= 120
        pdf = draft_invoice_pdf(snapshot)
        self.assertGreater(len(re.findall(rb"/Type /Page\b", pdf)), 2)
        decoded = b""
        for stream in re.findall(rb"stream\r?\n(.*?)endstream", pdf, re.S):
            decoded += zlib.decompress(base64.a85decode(stream.strip(), adobe=True))
        plain_text = b"".join(re.findall(rb"\(((?:\\.|[^\\)])*)\)\s*Tj", decoded))
        self.assertIn(b"NOT A TAX INVOICE", decoded)
        self.assertIn(b"<b>& literal markup</b>", plain_text)
        self.assertIn(b"INR 2,000.00", decoded)
        self.assertEqual(decoded.count(b"DRAFT - NOT A TAX INVOICE"), len(re.findall(rb"/Type /Page\b", pdf)))
        snapshot["seller"]["legal_name"] = "\u0915"
        with self.assertRaisesRegex(ValueError, "characters"):
            draft_invoice_pdf(snapshot)
        order.draft_invoice_snapshot = snapshot
        await mark_payment_captured(self.db, payment)
        response = await self.client.get(f"/me/orders/{order.order_number}/draft-invoice")
        self.assertEqual(response.status_code, 422)
        self.assertIn("characters", response.json()["detail"])
