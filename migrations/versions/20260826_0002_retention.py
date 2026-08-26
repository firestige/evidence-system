"""Add independent retention expiry markers.

Revision ID: 20260826_0002
Revises: 20260825_0001
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260826_0002"
down_revision: str | Sequence[str] | None = "20260825_0001"
branch_labels: str | Sequence[str] | None = ("retention",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "retention_expiry_markers",
        sa.Column("resource_class", sa.Text(), nullable=False),
        sa.Column("owner_key", sa.Text(), nullable=False),
        sa.Column("source_identity_kind", sa.Text(), nullable=False),
        sa.Column("source_identity_key", sa.Text(), nullable=False),
        sa.Column("resource_kind", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("compatibility", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("policy_revision", sa.Text(), nullable=False),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("batch_identity", sa.String(length=64), nullable=False),
        sa.CheckConstraint("resource_class IN ('RAW_DEBUG', 'TRACE_DETAIL', 'FACTUAL_PROJECTION')"),
        sa.CheckConstraint("batch_identity ~ '^[a-f0-9]{64}$'"),
        sa.PrimaryKeyConstraint("resource_class", "resource_kind", "owner_key"),
    )
    op.create_index(
        "ix_retention_expiry_markers_batch",
        "retention_expiry_markers",
        ["batch_identity"],
    )


def downgrade() -> None:
    op.drop_index("ix_retention_expiry_markers_batch", table_name="retention_expiry_markers")
    op.drop_table("retention_expiry_markers")
