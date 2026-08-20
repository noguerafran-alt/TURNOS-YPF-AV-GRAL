"""niveles de usuario y modelo de aeronave

Revision ID: c33ad79589eb
Revises: 22614788248f
Fecha: 2026-08-19 00:04:52.930649-03:00

REVISAR ANTES DE APLICAR:

  * Columna NOT NULL nueva sobre una tabla con datos: agregale server_default,
    o falla tanto en SQLite como en Postgres. Ejemplo:
        batch_op.add_column(sa.Column("campo", sa.String(40),
                                      nullable=False, server_default=""))
    Si no querés que el default quede en el esquema, sacalo después con
    batch_op.alter_column("campo", server_default=None).

  * Renombrar una columna: autogenerate lo detecta como "borrar + crear", o sea
    que PIERDE los datos. Cambialo a mano por alter_column(new_column_name=...).

  * Probá siempre `alembic upgrade head` y `alembic downgrade -1` sobre una copia
    con datos antes de tocar producción.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'c33ad79589eb'
down_revision: str | None = '22614788248f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Aplica el cambio."""
    # Columna nueva NOT NULL sobre una tabla con datos: necesita server_default,
    # si no falla tanto en SQLite como en Postgres.
    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("aircraft_model", sa.String(length=60), nullable=False, server_default="")
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("role", sa.String(length=20), nullable=False, server_default="cliente")
        )

    # Migración de datos: los que eran admin pasan a nivel 2, el resto queda
    # como cliente. Va ANTES de borrar is_admin, o se pierde la información.
    op.execute("UPDATE users SET role = 'nivel2' WHERE is_admin = 1")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_role"), ["role"], unique=False)
        batch_op.drop_column("is_admin")


def downgrade() -> None:
    """Revierte el cambio."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("0"))
        )

    # Vuelta atrás de los datos: los dos niveles administrativos eran is_admin.
    op.execute("UPDATE users SET is_admin = 1 WHERE role IN ('nivel1', 'nivel2')")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_role"))
        batch_op.drop_column("role")

    with op.batch_alter_table("bookings", schema=None) as batch_op:
        batch_op.drop_column("aircraft_model")
