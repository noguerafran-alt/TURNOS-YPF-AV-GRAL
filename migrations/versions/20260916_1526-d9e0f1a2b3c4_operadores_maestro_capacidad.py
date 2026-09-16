"""operadores maestro + abastecedoras.capacidad_l + bookings.operador_id

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Fecha: 2026-09-16 15:26:00-03:00
"""

from collections.abc import Sequence
import unicodedata

from alembic import op
import sqlalchemy as sa


revision: str = "d9e0f1a2b3c4"
down_revision: str | None = "c8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _norm_nombre(name: str) -> str:
    s = unicodedata.normalize("NFKC", (name or "").strip())
    s = " ".join(s.split())
    return s.casefold()


def upgrade() -> None:
    op.create_table(
        "operadores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nombre", sa.String(length=160), nullable=False),
        sa.Column("nombre_norm", sa.String(length=160), nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("nombre_norm"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_operadores_nombre_norm", "operadores", ["nombre_norm"])

    with op.batch_alter_table("abastecedoras", schema=None) as batch_op:
        batch_op.add_column(sa.Column("capacidad_l", sa.Integer(), nullable=True))

    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("operador_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_bookings_operador_id", ["operador_id"])
        batch_op.create_foreign_key(
            "fk_bookings_operador_id",
            "operadores",
            ["operador_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # Best-effort: bookings.operador_user_id → Operador maestro + operador_id
    bind = op.get_bind()
    users = bind.execute(
        sa.text("SELECT id, name, email FROM users WHERE id IN "
                "(SELECT DISTINCT operador_user_id FROM bookings WHERE operador_user_id IS NOT NULL)")
    ).fetchall()
    if users:
        existing_norms = {
            row[0]
            for row in bind.execute(sa.text("SELECT nombre_norm FROM operadores")).fetchall()
        }
        used_user_ids = {
            row[0]
            for row in bind.execute(
                sa.text("SELECT user_id FROM operadores WHERE user_id IS NOT NULL")
            ).fetchall()
        }
        for uid, name, email in users:
            display = (name or "").strip() or (email or "").split("@")[0] or f"user-{uid}"
            norm = _norm_nombre(display)
            if not norm:
                continue
            if norm in existing_norms:
                op_id = bind.execute(
                    sa.text("SELECT id FROM operadores WHERE nombre_norm = :n"),
                    {"n": norm},
                ).scalar()
            else:
                bind.execute(
                    sa.text(
                        "INSERT INTO operadores (nombre, nombre_norm, activo, user_id) "
                        "VALUES (:nombre, :norm, 1, :uid)"
                    ),
                    {
                        "nombre": display[:160],
                        "norm": norm,
                        "uid": uid if uid not in used_user_ids else None,
                    },
                )
                if uid not in used_user_ids:
                    used_user_ids.add(uid)
                existing_norms.add(norm)
                op_id = bind.execute(
                    sa.text("SELECT id FROM operadores WHERE nombre_norm = :n"),
                    {"n": norm},
                ).scalar()
            if op_id is not None:
                # Link user_id if row exists without link
                bind.execute(
                    sa.text(
                        "UPDATE operadores SET user_id = :uid "
                        "WHERE id = :oid AND user_id IS NULL "
                        "AND NOT EXISTS (SELECT 1 FROM operadores WHERE user_id = :uid)"
                    ),
                    {"uid": uid, "oid": op_id},
                )
                bind.execute(
                    sa.text(
                        "UPDATE bookings SET operador_id = :oid "
                        "WHERE operador_user_id = :uid AND operador_id IS NULL"
                    ),
                    {"oid": op_id, "uid": uid},
                )


def downgrade() -> None:
    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.drop_constraint("fk_bookings_operador_id", type_="foreignkey")
        batch_op.drop_index("ix_bookings_operador_id")
        batch_op.drop_column("operador_id")

    with op.batch_alter_table("abastecedoras", schema=None) as batch_op:
        batch_op.drop_column("capacidad_l")

    op.drop_index("ix_operadores_nombre_norm", table_name="operadores")
    op.drop_table("operadores")
