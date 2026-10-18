"""add inventory reservations

Revision ID: 7f3b2a1c9d44
Revises: c240491294f4
Create Date: 2026-10-18

"""
from alembic import op
import sqlalchemy as sa


revision = "7f3b2a1c9d44"
down_revision = "c240491294f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_locations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "inventory_levels",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("location_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("on_hand", sa.Integer(), nullable=False),
        sa.Column("reserved", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["inventory_locations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_id", "variant_id", name="uq_inventory_location_variant"),
    )
    op.create_index("ix_inventory_levels_location_id", "inventory_levels", ["location_id"])
    op.create_index("ix_inventory_levels_variant_id", "inventory_levels", ["variant_id"])
    op.create_table(
        "inventory_reservations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("inventory_level_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["inventory_level_id"], ["inventory_levels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inventory_reservations_inventory_level_id", "inventory_reservations", ["inventory_level_id"])
    op.create_index("ix_inventory_reservations_variant_id", "inventory_reservations", ["variant_id"])
    op.create_index("ix_inventory_reservations_order_id", "inventory_reservations", ["order_id"])
    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("inventory_level_id", sa.UUID(), nullable=False),
        sa.Column("variant_id", sa.UUID(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("movement_type", sa.String(length=40), nullable=False),
        sa.Column("quantity_delta", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["inventory_level_id"], ["inventory_levels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inventory_movements_inventory_level_id", "inventory_movements", ["inventory_level_id"])
    op.create_index("ix_inventory_movements_variant_id", "inventory_movements", ["variant_id"])
    op.create_index("ix_inventory_movements_order_id", "inventory_movements", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_inventory_movements_order_id", table_name="inventory_movements")
    op.drop_index("ix_inventory_movements_variant_id", table_name="inventory_movements")
    op.drop_index("ix_inventory_movements_inventory_level_id", table_name="inventory_movements")
    op.drop_table("inventory_movements")
    op.drop_index("ix_inventory_reservations_order_id", table_name="inventory_reservations")
    op.drop_index("ix_inventory_reservations_variant_id", table_name="inventory_reservations")
    op.drop_index("ix_inventory_reservations_inventory_level_id", table_name="inventory_reservations")
    op.drop_table("inventory_reservations")
    op.drop_index("ix_inventory_levels_variant_id", table_name="inventory_levels")
    op.drop_index("ix_inventory_levels_location_id", table_name="inventory_levels")
    op.drop_table("inventory_levels")
    op.drop_table("inventory_locations")
