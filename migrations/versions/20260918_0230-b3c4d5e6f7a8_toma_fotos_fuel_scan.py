"""toma_fotos + fuel_scan_log

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Fecha: 2026-09-18 02:30:00-03:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b3c4d5e6f7a8"
down_revision: str | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "toma_fotos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("matricula", sa.String(length=40), nullable=False),
        sa.Column("booking_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_toma_fotos_matricula", "toma_fotos", ["matricula"])
    op.create_index("ix_toma_fotos_booking_id", "toma_fotos", ["booking_id"])
    op.create_index("ix_toma_fotos_user_id", "toma_fotos", ["user_id"])

    op.create_table(
        "fuel_scan_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("booking_id", sa.Integer(), nullable=True),
        sa.Column("matricula", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("product_shown", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["booking_id"], ["bookings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_fuel_scan_log_booking_id", "fuel_scan_log", ["booking_id"])
    op.create_index("ix_fuel_scan_log_matricula", "fuel_scan_log", ["matricula"])
    op.create_index("ix_fuel_scan_log_user_id", "fuel_scan_log", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_fuel_scan_log_user_id", table_name="fuel_scan_log")
    op.drop_index("ix_fuel_scan_log_matricula", table_name="fuel_scan_log")
    op.drop_index("ix_fuel_scan_log_booking_id", table_name="fuel_scan_log")
    op.drop_table("fuel_scan_log")
    op.drop_index("ix_toma_fotos_user_id", table_name="toma_fotos")
    op.drop_index("ix_toma_fotos_booking_id", table_name="toma_fotos")
    op.drop_index("ix_toma_fotos_matricula", table_name="toma_fotos")
    op.drop_table("toma_fotos")
