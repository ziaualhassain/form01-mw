from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import jwt
from passlib.context import CryptContext

from app.core.config import get_settings


def _build_pwd_context() -> CryptContext:
    settings = get_settings()
    schemes = [settings.password_hash_scheme]
    for legacy_scheme in ("pbkdf2_sha256", "argon2"):
        if legacy_scheme not in schemes:
            schemes.append(legacy_scheme)
    return CryptContext(schemes=schemes, deprecated="auto")


pwd_context = _build_pwd_context()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def create_access_token(subject: str, roles: list[str]) -> str:
    settings = get_settings()
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {"sub": subject, "roles": roles, "exp": expires, "iat": datetime.now(timezone.utc)}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])
