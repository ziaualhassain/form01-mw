"""add product image gallery

Revision ID: b2d7e8f901ab
Revises: 9c41b7d8e2aa
Create Date: 2026-10-18

"""
from alembic import op
import sqlalchemy as sa


revision = "b2d7e8f901ab"
down_revision = "9c41b7d8e2aa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("image_urls", sa.JSON(), nullable=True))
    op.execute("UPDATE products SET image_urls = CASE WHEN image_url IS NOT NULL THEN json_build_array(image_url) ELSE '[]'::json END")
    op.alter_column("products", "image_urls", nullable=False, server_default=sa.text("'[]'::json"))


def downgrade() -> None:
    op.drop_column("products", "image_urls")
