from __future__ import annotations

import razorpay

from app.core.config import get_settings


def get_razorpay_client() -> razorpay.Client:
    settings = get_settings()
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise ValueError("Razorpay credentials are not configured")
    return razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))


def create_razorpay_order(*, amount_minor: int, currency: str, receipt: str, notes: dict[str, str] | None = None) -> dict:
    client = get_razorpay_client()
    return client.order.create(
        {
            "amount": int(amount_minor),
            "currency": currency,
            "receipt": receipt,
            "notes": notes or {},
        }
    )


def verify_razorpay_payment_signature(*, razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str) -> bool:
    client = get_razorpay_client()
    return bool(
        client.utility.verify_payment_signature(
            {
                "razorpay_order_id": razorpay_order_id,
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_signature": razorpay_signature,
            }
        )
    )


def fetch_razorpay_payment(payment_id: str) -> dict:
    return get_razorpay_client().payment.fetch(payment_id)


def create_razorpay_refund(*, payment_id: str, amount_minor: int, reason: str | None = None) -> dict:
    notes = {"reason": reason} if reason else {}
    return get_razorpay_client().payment.refund(
        payment_id,
        {"amount": int(amount_minor), "notes": notes},
    )


def verify_razorpay_webhook_signature(*, body: bytes, signature: str) -> bool:
    settings = get_settings()
    secret = settings.razorpay_webhook_secret or settings.razorpay_key_secret
    if not secret:
        raise ValueError("Razorpay webhook secret is not configured")
    client = get_razorpay_client()
    return bool(client.utility.verify_webhook_signature(body.decode("utf-8"), signature, secret))