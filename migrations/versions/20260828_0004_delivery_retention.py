"""Make Delivery the physical retention atom.

Revision ID: 20260828_0004
Revises: 20260826_0003
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0004"
down_revision: str | Sequence[str] | None = "20260826_0003"
branch_labels: str | Sequence[str] | None = ("delivery-retention",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "delivery_record_memberships",
        sa.Column("delivery_id", sa.Text(), nullable=False),
        sa.Column("identity_kind", sa.Text(), nullable=False),
        sa.Column("identity_key", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("identity_kind", "identity_key"),
        sa.ForeignKeyConstraint(
            ["identity_kind", "identity_key"],
            ["accepted_records.identity_kind", "accepted_records.identity_key"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_delivery_record_memberships_delivery",
        "delivery_record_memberships",
        ["delivery_id"],
    )
    op.create_table(
        "delivery_terminal_anchors",
        sa.Column("delivery_id", sa.Text(), nullable=False),
        sa.Column("identity_kind", sa.Text(), nullable=False),
        sa.Column("identity_key", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("delivery_id"),
        sa.ForeignKeyConstraint(
            ["identity_kind", "identity_key"],
            ["accepted_records.identity_kind", "accepted_records.identity_key"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_delivery_terminal_anchors_recorded",
        "delivery_terminal_anchors",
        ["recorded_at", "delivery_id"],
    )
    op.create_table(
        "delivery_retirement_fences",
        sa.Column("delivery_id", sa.Text(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_revision", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("delivery_id"),
    )
def downgrade() -> None:
    op.drop_table("delivery_retirement_fences")
    op.drop_index("ix_delivery_terminal_anchors_recorded", table_name="delivery_terminal_anchors")
    op.drop_table("delivery_terminal_anchors")
    op.drop_index(
        "ix_delivery_record_memberships_delivery", table_name="delivery_record_memberships"
    )
    op.drop_table("delivery_record_memberships")
