from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pydantic import BaseModel

from app.core.config import get_settings


POLICY_PACK_PATH = Path(__file__).resolve().parents[2] / "FORM01_Ecommerce_Policy_Pack.md"
POLICY_SECTIONS = {
    "terms-and-conditions": (1, "Terms & Conditions"),
    "shipping-and-delivery": (2, "Shipping & Delivery Policy"),
    "returns-and-refunds": (3, "Returns, Exchanges & Refunds Policy"),
    "privacy": (4, "Privacy Policy"),
    "payment-and-cancellation": (5, "Payment & Cancellation Policy"),
    "contact-and-grievance": (6, "Contact & Grievance Redressal"),
}


class PolicySummary(BaseModel):
    slug: str
    title: str
    published: bool


class PolicyDocument(PolicySummary):
    last_updated: str
    content_markdown: str


def published_policies() -> list[PolicyDocument]:
    approved_hash = get_settings().policy_pack_approved_sha256
    if not approved_hash:
        return []
    source = POLICY_PACK_PATH.read_bytes()
    if hashlib.sha256(source).hexdigest() != approved_hash:
        return []
    markdown = source.decode("utf-8")
    # Approval must not accidentally expose the pack's unfinished placeholders.
    if re.search(r"\\\[|\[(?![ xX]\])[^]\n]+\](?!\()", markdown):
        raise ValueError("Approved policy pack still contains unresolved placeholders")
    updated = re.search(r"^\*\*Last updated:\*\*\s*(.+)$", markdown, re.MULTILINE)
    if updated is None or not updated.group(1).strip():
        raise ValueError("Approved policy pack requires a last-updated date")
    documents = []
    for slug, (number, title) in POLICY_SECTIONS.items():
        section = re.search(
            rf"^## {number}\. [^\n]+\n(.*?)(?=^## |\Z)",
            markdown,
            re.MULTILINE | re.DOTALL,
        )
        if section is None or not section.group(1).strip():
            raise ValueError(f"Approved policy pack is missing {slug}")
        content = re.sub(r"\n-{3,}\s*$", "", section.group(1)).strip()
        documents.append(PolicyDocument(
            slug=slug, title=title, published=True,
            last_updated=updated.group(1).strip(), content_markdown=content,
        ))
    return documents
