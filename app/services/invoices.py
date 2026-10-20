from __future__ import annotations

from html import escape
from io import BytesIO
from collections.abc import Sequence

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.engine import Row

from app.db.models import Address, CartItem, Order, Product, ProductVariant
from app.services.gst import GstDraft, current_gst_draft


async def snapshot_draft_invoice(
    db: AsyncSession, order: Order,
    rows: Sequence[Row[tuple[CartItem, ProductVariant, Product]]], shipping_address: Address,
    configuration: tuple[int, GstDraft] | None = None, gst_test: dict | None = None,
) -> dict:
    version, config = configuration if configuration is not None else await current_gst_draft(db)
    if order.placed_at is None:
        raise ValueError("Order timestamp is required for a draft document")
    return {
        "schema_version": 1, "draft_only": True, "configuration_version": version,
        "seller": {"legal_name": config.legal_name, "address": config.address,
                   "gstin": config.gstin, "state_code": config.seller_state_code},
        "order_number": order.order_number, "placed_at": order.placed_at.isoformat(),
        "customer": {"name": shipping_address.recipient_name, "email": order.contact_email,
                     "address": ", ".join(str(value) for value in [
                         shipping_address.line1, shipping_address.line2, shipping_address.city,
                         shipping_address.state_or_region, shipping_address.postal_code,
                         shipping_address.country_code,
                     ] if value)},
        "currency": order.currency, "subtotal_minor": order.subtotal_minor,
        "discount_minor": order.discount_minor, "coupon_code": order.coupon_code,
        "shipping_minor": order.shipping_minor, "tax_minor": order.tax_minor, "total_minor": order.total_minor,
        "gst_test": gst_test,
        "lines": [{"title": product.title, "sku": variant.sku, "quantity": item.quantity,
                   "unit_price_minor": variant.price_minor,
                   "line_total_minor": variant.price_minor * item.quantity}
                  for item, variant, product in rows],
    }


def draft_invoice_pdf(snapshot: dict) -> bytes:
    output = BytesIO()
    styles = getSampleStyleSheet()
    document = SimpleDocTemplate(output, pagesize=(210 * mm, 297 * mm),
                                 leftMargin=18 * mm, rightMargin=18 * mm,
                                 topMargin=28 * mm, bottomMargin=22 * mm,
                                 title="FORM/01 Draft - Not a tax invoice")
    def paragraph(value, style="BodyText"):
        text = str(value)
        try:
            text.encode("cp1252")
        except UnicodeEncodeError as exc:
            raise ValueError("This draft PDF cannot represent some characters in the order. "
                             "Contact support for a document with an appropriate font; no characters have been replaced.") from exc
        return Paragraph(escape(text).replace("\n", "<br/>"), styles[style])
    def money(value):
        return f"{snapshot['currency']} {value / 100:,.2f}"
    seller, customer = snapshot["seller"], snapshot["customer"]
    story = [
        paragraph("FORM/01 - Draft order document", "Title"),
        paragraph("NOT A TAX INVOICE. No legal invoice number has been issued.", "Heading2"),
        paragraph("LOCAL GST TEST ONLY. Draft GST was included in the Razorpay Test amount. "
                  "No real tax collected. Registration, rates and invoice numbering remain unconfirmed."
                  if snapshot.get("gst_test") else
                  "GST registration, rates and invoice numbering are unconfirmed. "
                  "GST collection is disabled; draft rules have NOT been applied to this order."),
        Spacer(1, 5 * mm),
        paragraph(f"Order reference: {snapshot['order_number']}"),
        paragraph(f"Placed at (timestamp with timezone): {snapshot['placed_at']}"),
        paragraph(f"Draft settings version: {snapshot['configuration_version']}"),
        Spacer(1, 4 * mm), paragraph("Seller (unverified draft details)", "Heading3"),
        paragraph(seller["legal_name"] or "Not provided"),
        paragraph(seller["address"] or "Registered address not confirmed"),
        paragraph(f"GSTIN (unverified): {seller['gstin'] or 'Not confirmed'}"),
        paragraph(f"Seller state code: {seller['state_code']}"),
        Spacer(1, 4 * mm), paragraph("Customer / delivery address", "Heading3"),
        paragraph(customer["name"]), paragraph(customer["email"]), paragraph(customer["address"]),
        Spacer(1, 5 * mm),
    ]
    table_rows = [[paragraph(text) for text in ["Item / SKU", "Qty", "Unit price", "Line subtotal"]]]
    for line in snapshot["lines"]:
        table_rows.append([paragraph(f"{line['title']}\n{line['sku']}"), paragraph(line["quantity"]),
                           paragraph(money(line["unit_price_minor"])), paragraph(money(line["line_total_minor"]))])
    table = Table(table_rows, colWidths=[77 * mm, 12 * mm, 40 * mm, 40 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf1ea")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LINEBELOW", (0, 0), (-1, -1), .4, colors.HexColor("#d8ddd3")),
    ]))
    story.extend([table, Spacer(1, 5 * mm)])
    for label, value in [
        ("Product subtotal", snapshot["subtotal_minor"]),
        (f"Discount ({snapshot['coupon_code'] or 'none'})", -snapshot["discount_minor"]),
        ("Shipping", snapshot["shipping_minor"]),
        ("GST TEST ONLY (not real tax)" if snapshot.get("gst_test") else "GST collected (disabled, not zero-rated classification)", snapshot["tax_minor"]),
        ("Order total", snapshot["total_minor"]),
    ]:
        story.append(paragraph(f"{label}: {money(value)}"))
    if snapshot.get("gst_test"):
        test = snapshot["gst_test"]
        story.extend([
            Spacer(1, 4 * mm), paragraph("Local GST test breakdown - unapproved draft rules", "Heading3"),
            paragraph(f"Destination state: {test['destination_state_code']} / India date: {test['on_date']}"),
            paragraph(f"TEST CGST: {money(test['cgst_minor'])} / TEST SGST: {money(test['sgst_minor'])} / TEST IGST: {money(test['igst_minor'])}"),
        ])
        for line in test["lines"]:
            story.append(paragraph(
                f"{line['sku']} / HSN {line['hsn']} / Draft rate {line['rate_basis_points'] / 100}% / "
                f"Taxable {money(line['taxable_minor'])} / TEST tax {money(line['tax_minor'])}"
            ))
    story.extend([Spacer(1, 5 * mm), paragraph(
        "This draft reflects the original order amounts, not a payment receipt, "
        "credit note, refund statement or GST filing document. Check current payment "
        "and refund status in Your Orders. HSN and GST components remain unapproved."
    )])
    def page_label(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(18 * mm, 283 * mm, "GST TEST ONLY - NOT A TAX INVOICE" if snapshot.get("gst_test") else "DRAFT - NOT A TAX INVOICE")
        canvas.setFont("Helvetica", 8)
        canvas.drawString(18 * mm, 12 * mm, f"Draft only | Page {doc.page}")
        canvas.restoreState()
    document.build(story, onFirstPage=page_label, onLaterPages=page_label)
    return output.getvalue()
