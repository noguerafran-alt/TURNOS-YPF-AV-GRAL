"""hangares + campos extra en matriculas_combustible

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Fecha: 2026-09-16 18:35:00-03:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e0f1a2b3c4d5"
down_revision: str | None = "d9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hangares",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("codigo", sa.String(length=40), nullable=False),
        sa.Column("nombre", sa.String(length=160), nullable=False),
        sa.Column("agenda_id", sa.Integer(), nullable=True),
        sa.Column("capacidad", sa.Integer(), nullable=True),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["agenda_id"], ["agendas.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("codigo"),
    )
    op.create_index("ix_hangares_codigo", "hangares", ["codigo"])
    op.create_index("ix_hangares_agenda_id", "hangares", ["agenda_id"])

    with op.batch_alter_table("matriculas_combustible", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tipo", sa.String(length=60), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("motor", sa.String(length=60), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("cliente", sa.String(length=200), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("hangar_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("capacidad_l", sa.Integer(), nullable=True))
        batch_op.create_index("ix_matriculas_combustible_hangar_id", ["hangar_id"])
        batch_op.create_foreign_key(
            "fk_matriculas_combustible_hangar_id",
            "hangares",
            ["hangar_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("matriculas_combustible", schema=None) as batch_op:
        batch_op.drop_constraint("fk_matriculas_combustible_hangar_id", type_="foreignkey")
        batch_op.drop_index("ix_matriculas_combustible_hangar_id")
        batch_op.drop_column("capacidad_l")
        batch_op.drop_column("hangar_id")
        batch_op.drop_column("cliente")
        batch_op.drop_column("motor")
        batch_op.drop_column("tipo")

    op.drop_index("ix_hangares_agenda_id", table_name="hangares")
    op.drop_index("ix_hangares_codigo", table_name="hangares")
    op.drop_table("hangares")
