"""user_agendas — coordinador scoped por aeroplanta

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Fecha: 2026-09-18 08:16:00-03:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_agendas",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("agenda_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agenda_id"], ["agendas.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "agenda_id"),
        sa.UniqueConstraint("user_id", "agenda_id", name="uq_user_agendas_user_agenda"),
    )
    op.create_index("ix_user_agendas_user_id", "user_agendas", ["user_id"])
    op.create_index("ix_user_agendas_agenda_id", "user_agendas", ["agenda_id"])


def downgrade() -> None:
    op.drop_index("ix_user_agendas_agenda_id", table_name="user_agendas")
    op.drop_index("ix_user_agendas_user_id", table_name="user_agendas")
    op.drop_table("user_agendas")
