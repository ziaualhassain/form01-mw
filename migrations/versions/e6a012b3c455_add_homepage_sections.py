"""add homepage sections

Revision ID: e6a012b3c455
Revises: d5f9012a3b44
Create Date: 2026-10-18

"""
from alembic import op
import sqlalchemy as sa


revision = "e6a012b3c455"
down_revision = "d5f9012a3b44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "homepage_sections",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("section_key", sa.String(length=120), nullable=False),
        sa.Column("section_type", sa.String(length=50), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("publish_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unpublish_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("section_key", name="uq_homepage_section_key"),
    )


def downgrade() -> None:
    op.drop_table("homepage_sections")
