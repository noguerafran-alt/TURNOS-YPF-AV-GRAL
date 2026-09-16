"""mis aeronaves: user_aircraft personal list

Revision ID: b7e8f9a0c1d2
Revises: a1b2c3d4e5f6
Fecha: 2026-09-16 14:12:00-03:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b7e8f9a0c1d2"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_aircraft",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("matricula", sa.String(length=40), nullable=False),
        sa.Column("matricula_display", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("modelo", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("tipo", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("combustible", sa.String(length=80), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "matricula", name="uq_user_aircraft_user_matricula"),
    )
    op.create_index("ix_user_aircraft_user_id", "user_aircraft", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_aircraft_user_id", table_name="user_aircraft")
    op.drop_table("user_aircraft")
