"""matriculas_combustible: columna modelo opcional (maestro)

Revision ID: c8d9e0f1a2b3
Revises: b7e8f9a0c1d2
Fecha: 2026-09-16 15:05:00-03:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c8d9e0f1a2b3"
down_revision: str | None = "b7e8f9a0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("matriculas_combustible", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("modelo", sa.String(length=80), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table("matriculas_combustible", schema=None) as batch_op:
        batch_op.drop_column("modelo")
