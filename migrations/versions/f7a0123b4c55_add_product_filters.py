"""add product filters

Revision ID: f7a0123b4c55
Revises: e6a012b3c455
Create Date: 2026-10-18

"""
from alembic import op
import sqlalchemy as sa


revision = "f7a0123b4c55"
down_revision = "e6a012b3c455"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("gender", sa.String(length=30), nullable=True))
    op.add_column("products", sa.Column("theme", sa.String(length=80), nullable=True))
    op.create_index("ix_products_gender", "products", ["gender"])
    op.create_index("ix_products_theme", "products", ["theme"])


def downgrade() -> None:
    op.drop_index("ix_products_theme", table_name="products")
    op.drop_index("ix_products_gender", table_name="products")
    op.drop_column("products", "theme")
    op.drop_column("products", "gender")
