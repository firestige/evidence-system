"""Create atomic admission and projection core.

Revision ID: 20260825_0001
Revises:
Create Date: 2026-08-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260825_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("core",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accepted_records",
        sa.Column("identity_kind", sa.Text(), nullable=False),
        sa.Column("identity_key", sa.Text(), nullable=False),
        sa.Column("canonical_digest", sa.String(length=64), nullable=False),
        sa.Column("profile_version", sa.Text(), nullable=False),
        sa.Column("family_schema", sa.Text(), nullable=True),
        sa.Column("logical_record", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("identity_kind IN ('event', 'span')"),
        sa.CheckConstraint("canonical_digest ~ '^[a-f0-9]{64}$'"),
        sa.PrimaryKeyConstraint("identity_kind", "identity_key"),
    )
    op.create_table(
        "projection_effects",
        sa.Column("effect_kind", sa.Text(), nullable=False),
        sa.Column("effect_key", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_identity_kind", sa.Text(), nullable=False),
        sa.Column("source_identity_key", sa.Text(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("effect_kind", "effect_key"),
        sa.ForeignKeyConstraint(
            ["source_identity_kind", "source_identity_key"],
            ["accepted_records.identity_kind", "accepted_records.identity_key"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_projection_effects_kind_recorded",
        "projection_effects",
        ["effect_kind", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_projection_effects_kind_recorded", table_name="projection_effects")
    op.drop_table("projection_effects")
    op.drop_table("accepted_records")
