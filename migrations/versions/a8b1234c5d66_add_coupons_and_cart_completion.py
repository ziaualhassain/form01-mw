"""Add coupons, usage reservations and payment-confirmed cart cleanup."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a8b1234c5d66"
down_revision = "f7a0123b4c55"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade():
    op.create_table(
        "coupons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(40), unique=True, nullable=False),
        sa.Column("discount_type", sa.String(20), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("max_discount_minor", sa.Integer()),
        sa.Column("min_subtotal_minor", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("usage_limit", sa.Integer()),
        sa.Column("per_customer_limit", sa.Integer()),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *timestamps(),
    )
    op.add_column("orders", sa.Column("coupon_code", sa.String(40)))
    op.add_column("orders", sa.Column("cart_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("carts.id", ondelete="SET NULL")))
    op.add_column("orders", sa.Column("cart_items_snapshot", sa.JSON()))
    op.add_column("orders", sa.Column("cart_cleared_at", sa.DateTime(timezone=True)))
    op.create_table(
        "coupon_redemptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("coupon_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("coupons.id"), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_coupon_redemptions_coupon_id", "coupon_redemptions", ["coupon_id"])
    op.create_index("ix_coupon_redemptions_user_id", "coupon_redemptions", ["user_id"])


def downgrade():
    op.drop_table("coupon_redemptions")
    for column in ["cart_cleared_at", "cart_items_snapshot", "cart_id", "coupon_code"]:
        op.drop_column("orders", column)
    op.drop_table("coupons")
