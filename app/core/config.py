from __future__ import annotations

from functools import lru_cache
import ssl
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="FORM/01 Middleware")
    environment: str = Field(default="development")
    gst_checkout_test_mode: bool = Field(default=False, alias="GST_CHECKOUT_TEST_MODE")
    policy_pack_approved_sha256: str | None = Field(default=None, alias="POLICY_PACK_APPROVED_SHA256")
    secret_key: str = Field(default="change-me-in-production")
    access_token_expire_minutes: int = Field(default=60 * 24)
    pending_payment_timeout_minutes: int = Field(default=15, alias="PENDING_PAYMENT_TIMEOUT_MINUTES")
    payment_expiry_interval_seconds: int = Field(default=60, alias="PAYMENT_EXPIRY_INTERVAL_SECONDS")
    password_hash_scheme: str = Field(default="pbkdf2_sha256", alias="PASSWORD_HASH_SCHEME")
    cors_origins: list[str] = Field(default_factory=lambda: [
        "http://localhost:3000",
        "http://localhost:3001",
        "https://form01-admin.vercel.app",
        "https://form01-store-xi.vercel.app",
    ])
    razorpay_key_id: str | None = Field(default=None, alias="RAZORPAY_KEY_ID")
    razorpay_key_secret: str | None = Field(default=None, alias="RAZORPAY_KEY_SECRET")
    razorpay_webhook_secret: str | None = Field(default=None, alias="RAZORPAY_WEBHOOK_SECRET")

    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    aiven_database_url: str | None = Field(default=None, alias="AIVEN_DATABASE_URL")
    service_uri: str | None = Field(default=None, alias="SERVICE_URI")
    postgres_host: str | None = Field(default=None, alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    postgres_db: str | None = Field(default=None, alias="POSTGRES_DB")
    postgres_user: str | None = Field(default=None, alias="POSTGRES_USER")
    postgres_password: str | None = Field(default=None, alias="POSTGRES_PASSWORD")
    postgres_sslmode: str = Field(default="require", alias="POSTGRES_SSLMODE")

    @model_validator(mode="after")
    def validate_gst_test_mode(self) -> Settings:
        if self.gst_checkout_test_mode and (
            self.environment != "development"
            or not self.razorpay_configured
            or not (self.razorpay_key_id or "").startswith("rzp_test_")
        ):
            raise ValueError("GST_CHECKOUT_TEST_MODE requires ENVIRONMENT=development and configured Razorpay Test keys")
        return self

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return []

    @property
    def database_url_resolved(self) -> str:
        url = self.database_url or self.aiven_database_url or self.service_uri
        if url:
            normalized = url
            if normalized.startswith("postgres://"):
                normalized = normalized.replace("postgres://", "postgresql+asyncpg://", 1)
            elif normalized.startswith("postgresql://"):
                normalized = normalized.replace("postgresql://", "postgresql+asyncpg://", 1)

            parsed = urlparse(normalized)
            if parsed.scheme.startswith("postgresql+asyncpg") and parsed.query:
                query = [(key, value) for key, value in parse_qsl(parsed.query) if key.lower() != "sslmode"]
                normalized = urlunparse(parsed._replace(query=urlencode(query)))
            return normalized
        if all([self.postgres_host, self.postgres_db, self.postgres_user, self.postgres_password]):
            return (
                f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return "sqlite+aiosqlite:///./form01.db"

    @property
    def database_connect_args(self) -> dict[str, Any]:
        url = self.database_url or self.aiven_database_url or self.service_uri
        ca_file = PROJECT_ROOT / "ca.pem"

        def build_ssl_context() -> ssl.SSLContext:
            context = ssl.create_default_context()
            if ca_file.exists():
                context.load_verify_locations(cafile=str(ca_file))
            return context

        if url:
            parsed = urlparse(url)
            query = dict(parse_qsl(parsed.query))
            if query.get("sslmode", "").lower() == "require":
                return {"ssl": build_ssl_context()}
            return {}
        if self.database_url_resolved.startswith("sqlite"):
            return {}
        if self.postgres_sslmode.lower() == "require":
            return {"ssl": build_ssl_context()}
        return {}

    @property
    def razorpay_configured(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
