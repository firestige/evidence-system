"""Preserve the exact retention eligibility instant.

Revision ID: 20260826_0003
Revises: 20260826_0002
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260826_0003"
down_revision: str | Sequence[str] | None = "20260826_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "retention_expiry_markers",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE retention_expiry_markers SET expires_at = expired_at")
    op.alter_column("retention_expiry_markers", "expires_at", nullable=False)


def downgrade() -> None:
    op.drop_column("retention_expiry_markers", "expires_at")
