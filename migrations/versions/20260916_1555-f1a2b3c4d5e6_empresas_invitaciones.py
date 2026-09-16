"""empresas + invitaciones + membresía user + flag booking

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Fecha: 2026-09-16 15:55:00-03:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e0f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "empresas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nombre", sa.String(length=160), nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("nombre"),
    )
    op.create_index("ix_empresas_nombre", "empresas", ["nombre"])

    op.create_table(
        "invitaciones_empresa",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("empresa_id", sa.Integer(), nullable=False),
        sa.Column("invited_by", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("role_in_empresa", sa.String(length=30), nullable=False, server_default="usuario_empresa"),
        sa.Column("token", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["empresa_id"], ["empresas.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("token"),
    )
    op.create_index("ix_invitaciones_empresa_email", "invitaciones_empresa", ["email"])
    op.create_index("ix_invitaciones_empresa_empresa_id", "invitaciones_empresa", ["empresa_id"])
    op.create_index("ix_invitaciones_empresa_status", "invitaciones_empresa", ["status"])
    op.create_index(
        "ix_invitaciones_empresa_email_status",
        "invitaciones_empresa",
        ["email", "status"],
    )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("empresa_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("role_in_empresa", sa.String(length=30), nullable=True))
        batch_op.create_index("ix_users_empresa_id", ["empresa_id"])
        batch_op.create_foreign_key(
            "fk_users_empresa_id",
            "empresas",
            ["empresa_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "matricula_otra_empresa",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.drop_column("matricula_otra_empresa")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint("fk_users_empresa_id", type_="foreignkey")
        batch_op.drop_index("ix_users_empresa_id")
        batch_op.drop_column("role_in_empresa")
        batch_op.drop_column("empresa_id")

    op.drop_index("ix_invitaciones_empresa_email_status", table_name="invitaciones_empresa")
    op.drop_index("ix_invitaciones_empresa_status", table_name="invitaciones_empresa")
    op.drop_index("ix_invitaciones_empresa_empresa_id", table_name="invitaciones_empresa")
    op.drop_index("ix_invitaciones_empresa_email", table_name="invitaciones_empresa")
    op.drop_table("invitaciones_empresa")

    op.drop_index("ix_empresas_nombre", table_name="empresas")
    op.drop_table("empresas")
