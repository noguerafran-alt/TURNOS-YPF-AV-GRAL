"""Entorno de Alembic.

Toma la URL de la base de la config de la app (DATABASE_URL), así migrar y correr
la app siempre apuntan al mismo lugar y no hay credenciales en el repositorio.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.database import Base

# Importar los modelos registra las tablas en Base.metadata. Sin esto,
# autogenerate no ve nada y genera migraciones vacías.
from app import models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# La URL viene de la app, no del alembic.ini
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

target_metadata = Base.metadata

# SQLite no soporta la mayoría de los ALTER TABLE. En modo batch, Alembic recrea
# la tabla y copia los datos, así las mismas migraciones sirven en desarrollo
# (SQLite) y en producción (Postgres).
IS_SQLITE = settings.database_url.startswith("sqlite")


def render_item(type_, obj, autogen_context):
    """Cómo se escriben los tipos en el archivo de migración.

    Sin esto, nuestro UTCDateTime se renderiza como `app.models.UTCDateTime(...)`
    y la migración queda atada al código de la app: si mañana movés o renombrás
    esa clase, las migraciones viejas dejan de correr. Como en la base es un
    timestamp común y corriente, se escribe como sa.DateTime(timezone=True).
    """
    if type_ == "type" and obj.__class__.__name__ == "UTCDateTime":
        return "sa.DateTime(timezone=True)"
    return False  # el resto lo renderiza Alembic


def run_migrations_offline() -> None:
    """Genera el SQL sin conectarse (alembic upgrade head --sql)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_item=render_item,
        render_as_batch=IS_SQLITE,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Aplica las migraciones contra la base."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,        # detecta cambios de tipo de columna
            render_item=render_item,
            render_as_batch=IS_SQLITE,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
