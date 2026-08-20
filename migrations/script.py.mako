"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Fecha: ${create_date}

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
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    """Aplica el cambio."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Revierte el cambio."""
    ${downgrades if downgrades else "pass"}
