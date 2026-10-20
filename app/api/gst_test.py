from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from app.core.config import get_settings


def require_local_gst_checkout(request: Request) -> None:
    if not get_settings().gst_checkout_test_mode:
        return
    try:
        local_peer = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local_peer = False
    origin = request.headers.get("origin")
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    forwarded = any(name in request.headers for name in ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto"))
    if (
        not local_peer
        or request.url.hostname not in local_hosts
        or (origin is not None and urlsplit(origin).hostname not in local_hosts)
        or forwarded
    ):
        raise HTTPException(status_code=403, detail="GST test checkout is available only through direct localhost requests. Do not use a proxy or public deployment.")
