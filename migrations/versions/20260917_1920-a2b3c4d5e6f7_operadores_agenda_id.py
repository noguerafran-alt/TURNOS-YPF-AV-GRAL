"""operadores.agenda_id (ámbito Global / planta)

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Fecha: 2026-09-17 19:20:00-03:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a2b3c4d5e6f7"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("operadores", schema=None) as batch_op:
        batch_op.add_column(sa.Column("agenda_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_operadores_agenda_id", ["agenda_id"])
        batch_op.create_foreign_key(
            "fk_operadores_agenda_id",
            "agendas",
            ["agenda_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("operadores", schema=None) as batch_op:
        batch_op.drop_constraint("fk_operadores_agenda_id", type_="foreignkey")
        batch_op.drop_index("ix_operadores_agenda_id")
        batch_op.drop_column("agenda_id")
