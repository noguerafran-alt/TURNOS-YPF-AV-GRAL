"""Motor de base de datos y sesiones de SQLAlchemy.

Soporta las dos configuraciones:

  * **SQLite sobre disco persistente** (lo que usamos en Render): un solo archivo
    en el disco montado. Requiere una única instancia del servicio, porque el
    disco no se puede compartir entre procesos de distintas máquinas.
  * **PostgreSQL**: si algún día hace falta escalar a varias instancias, alcanza
    con cambiar DATABASE_URL. El resto del código ya está preparado.
"""

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")

if _is_sqlite:
    # Render monta el disco vacío: si la carpeta no existe, SQLite falla al abrir.
    db_path = settings.database_url.split("sqlite:///", 1)[-1]
    if db_path and db_path != ":memory:":
        Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args=(
        {
            # FastAPI atiende cada request en un hilo distinto del pool
            "check_same_thread": False,
            # Si otro hilo está escribiendo, espera en vez de tirar "database is locked"
            "timeout": 30,
        }
        if _is_sqlite
        else {}
    ),
    pool_pre_ping=True,  # reconecta si Postgres cortó la conexión ociosa
    echo=False,
)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        """Ajustes que hacen a SQLite usable como base de producción."""
        cursor = dbapi_connection.cursor()
        # WAL: los lectores no bloquean al escritor ni viceversa. Es la diferencia
        # entre soportar varios clientes reservando a la vez o no.
        cursor.execute("PRAGMA journal_mode=WAL")
        # Espera hasta 30 s si la base está ocupada, en vez de fallar al instante
        cursor.execute("PRAGMA busy_timeout=30000")
        # NORMAL + WAL es seguro ante caídas de la app; solo un corte de luz del
        # servidor podría perder la última transacción. Con FULL cada commit
        # hace fsync y el sistema se vuelve notablemente más lento.
        cursor.execute("PRAGMA synchronous=NORMAL")
        # SQLite ignora las claves foráneas salvo que se pidan explícitamente
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """Dependencia de FastAPI: una sesión por request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def is_postgres() -> bool:
    return engine.dialect.name == "postgresql"
