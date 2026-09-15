"""panel coordinador: abastecedoras, matriculas_combustible, booking extensions

Revision ID: a1b2c3d4e5f6
Revises: 7c2f13298ed3
Fecha: 2026-09-15 20:19:00-03:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "7c2f13298ed3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "abastecedoras",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nombre", sa.String(length=80), nullable=False),
        sa.Column("codigo", sa.String(length=40), nullable=True),
        sa.Column("grado", sa.String(length=40), nullable=False),
        sa.Column("agenda_id", sa.Integer(), nullable=True),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("fuera_de_servicio_hasta", sa.Date(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["agenda_id"], ["agendas.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("codigo"),
    )
    op.create_index("ix_abastecedoras_agenda_id", "abastecedoras", ["agenda_id"])

    op.create_table(
        "matriculas_combustible",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("matricula", sa.String(length=40), nullable=False),
        sa.Column("matricula_display", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("combustible", sa.String(length=80), nullable=True),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("matricula"),
    )
    op.create_index("ix_matriculas_combustible_matricula", "matriculas_combustible", ["matricula"])

    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("coordinacion_status", sa.String(length=20), nullable=False, server_default="PENDIENTE"))
        batch_op.add_column(sa.Column("origen", sa.String(length=20), nullable=False, server_default="WEB"))
        batch_op.add_column(sa.Column("sobreturno", sa.Boolean(), nullable=False, server_default=sa.text("0")))
        batch_op.add_column(sa.Column("abastecedora_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("operador_user_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("combustible_declarado", sa.String(length=80), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("primera_carga", sa.Boolean(), nullable=False, server_default=sa.text("0")))
        batch_op.add_column(sa.Column("unknown_matricula", sa.Boolean(), nullable=False, server_default=sa.text("0")))
        batch_op.add_column(sa.Column("combustible_reconfirmado_en_persona", sa.Boolean(), nullable=False, server_default=sa.text("0")))
        batch_op.add_column(sa.Column("reconfirm_pregunte_en_persona", sa.Boolean(), nullable=False, server_default=sa.text("0")))
        batch_op.add_column(sa.Column("reconfirm_coincide_declarado", sa.Boolean(), nullable=False, server_default=sa.text("0")))
        batch_op.add_column(sa.Column("reconfirmado_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("reconfirmado_by", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("abastecido_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("ausente_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("ausente_motivo", sa.String(length=300), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("cancelado_coord_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("cancelado_motivo", sa.String(length=300), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("asignado_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("asignado_by", sa.Integer(), nullable=True))
        batch_op.create_index("ix_bookings_coordinacion_status", ["coordinacion_status"])
        batch_op.create_index("ix_bookings_abastecedora_id", ["abastecedora_id"])
        batch_op.create_index("ix_bookings_operador_user_id", ["operador_user_id"])
        batch_op.create_foreign_key("fk_bookings_abastecedora_id", "abastecedoras", ["abastecedora_id"], ["id"], ondelete="SET NULL")
        batch_op.create_foreign_key("fk_bookings_operador_user_id", "users", ["operador_user_id"], ["id"], ondelete="SET NULL")
        batch_op.create_foreign_key("fk_bookings_reconfirmado_by", "users", ["reconfirmado_by"], ["id"], ondelete="SET NULL")
        batch_op.create_foreign_key("fk_bookings_asignado_by", "users", ["asignado_by"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.drop_constraint("fk_bookings_asignado_by", type_="foreignkey")
        batch_op.drop_constraint("fk_bookings_reconfirmado_by", type_="foreignkey")
        batch_op.drop_constraint("fk_bookings_operador_user_id", type_="foreignkey")
        batch_op.drop_constraint("fk_bookings_abastecedora_id", type_="foreignkey")
        batch_op.drop_index("ix_bookings_operador_user_id")
        batch_op.drop_index("ix_bookings_abastecedora_id")
        batch_op.drop_index("ix_bookings_coordinacion_status")
        for col in [
            "asignado_by", "asignado_at", "cancelado_motivo", "cancelado_coord_at",
            "ausente_motivo", "ausente_at", "abastecido_at", "reconfirmado_by",
            "reconfirmado_at", "reconfirm_coincide_declarado", "reconfirm_pregunte_en_persona",
            "combustible_reconfirmado_en_persona", "unknown_matricula", "primera_carga",
            "combustible_declarado", "operador_user_id", "abastecedora_id", "sobreturno",
            "origen", "coordinacion_status",
        ]:
            batch_op.drop_column(col)

    op.drop_index("ix_matriculas_combustible_matricula", table_name="matriculas_combustible")
    op.drop_table("matriculas_combustible")
    op.drop_index("ix_abastecedoras_agenda_id", table_name="abastecedoras")
    op.drop_table("abastecedoras")
