"""Versioned draft GST settings and immutable draft invoice snapshots."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c0d3456e7f88"
down_revision = "b9c2345d6e77"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gst_configurations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column("orders", sa.Column("draft_invoice_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "draft_invoice_snapshot")
    op.drop_table("gst_configurations")
