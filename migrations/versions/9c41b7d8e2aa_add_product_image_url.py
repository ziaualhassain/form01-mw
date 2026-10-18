"""add product image url

Revision ID: 9c41b7d8e2aa
Revises: 7f3b2a1c9d44
Create Date: 2026-10-18

"""
from alembic import op
import sqlalchemy as sa


revision = "9c41b7d8e2aa"
down_revision = "7f3b2a1c9d44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("image_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "image_url")
