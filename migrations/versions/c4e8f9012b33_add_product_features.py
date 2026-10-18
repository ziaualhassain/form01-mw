"""add product features

Revision ID: c4e8f9012b33
Revises: b2d7e8f901ab
Create Date: 2026-10-18

"""
from alembic import op
import sqlalchemy as sa


revision = "c4e8f9012b33"
down_revision = "b2d7e8f901ab"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("features", sa.JSON(), nullable=True))
    op.execute("UPDATE products SET features = '[]'::json")
    op.alter_column("products", "features", nullable=False, server_default=sa.text("'[]'::json"))


def downgrade() -> None:
    op.drop_column("products", "features")
