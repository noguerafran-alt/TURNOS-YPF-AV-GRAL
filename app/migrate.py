"""Aplicación de migraciones desde código.

La app corre `alembic upgrade head` al arrancar. Con una sola instancia (el plan
de Render que usamos) es lo más simple y seguro: la base siempre queda al día sin
pasos manuales después de cada deploy.

Si algún día escalás a varias instancias, conviene sacar esto y correr las
migraciones una sola vez antes del deploy (`preDeployCommand` en Render), para que
dos procesos no intenten migrar a la vez.
"""

import logging
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from app import models  # noqa: F401  (registra las tablas en Base.metadata)
from app.database import Base, engine

logger = logging.getLogger("turnos.migrate")

BASE_DIR = Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    config = Config(str(BASE_DIR / "alembic.ini"))
    # Rutas absolutas: el cron y la app pueden arrancar desde otro directorio
    config.set_main_option("script_location", str(BASE_DIR / "migrations"))
    return config


def _matches_models() -> bool:
    """¿El esquema de la base ya es igual al que describen los modelos?"""
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        return not compare_metadata(context, Base.metadata)


def upgrade_database() -> None:
    """Deja la base en la última revisión."""
    config = _alembic_config()
    tables = set(inspect(engine).get_table_names())

    # Caso de transición: bases creadas con create_all antes de que existiera
    # Alembic. Tienen las tablas pero no el registro de versión, así que `upgrade`
    # fallaría al intentar crear lo que ya está.
    #
    # En qué revisión marcarlas no se puede adivinar: depende de cuándo se creó
    # esa base. Se compara el esquema real contra los modelos y se decide:
    #   * ya coincide con el último  -> se marca en head, no hay nada que aplicar
    #   * no coincide                -> se marca en la inicial y se aplica el resto
    if tables and "alembic_version" not in tables:
        if _matches_models():
            logger.warning(
                "Base preexistente sin historial de migraciones: su esquema ya coincide "
                "con los modelos, se marca en head."
            )
            command.stamp(config, "head")
            return

        initial = ScriptDirectory.from_config(config).get_bases()
        if initial:
            logger.warning(
                "Base preexistente sin historial de migraciones: se marca en la revisión "
                "inicial (%s) y se aplican las siguientes.",
                initial[0],
            )
            command.stamp(config, initial[0])

    command.upgrade(config, "head")
    logger.info("Base de datos al día.")
