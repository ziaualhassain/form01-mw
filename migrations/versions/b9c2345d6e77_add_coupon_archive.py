"""Preserve archived coupon history."""

from alembic import op
import sqlalchemy as sa

revision = "b9c2345d6e77"
down_revision = "a8b1234c5d66"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("coupons", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("coupons", "archived_at")
