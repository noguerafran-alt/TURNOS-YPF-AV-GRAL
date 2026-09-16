"""Punto de entrada de la aplicación.

Correr en local:
    uvicorn app.main:app --reload
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth import get_current_user  # noqa: F401  (se usa como dependencia en los routers)
from app.config import settings
from app.migrate import upgrade_database
from app.reminders import reminder_loop
from app.routers import admin, auth_routes, bookings, coord, external_ypf, maestros, mis_aeronaves, operador, public, users_admin
from app.templating import templates

# Uvicorn configura sus propios loggers, pero deja el root sin handlers: sin esto,
# los mensajes de la app (migraciones, recordatorios, emails) no aparecerían en los
# logs de Render.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("turnos")

# Falla temprano si producción quedó mal configurada
settings.validate()

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Las tablas las crea y modifica Alembic, nunca create_all: así un cambio de
    # esquema no obliga a borrar la base ni pierde los turnos ya reservados.
    if settings.run_migrations:
        upgrade_database()

    # Los recordatorios corren adentro de la app: con SQLite sobre un disco de
    # Render no se puede usar un Cron Job aparte (el disco va en un solo servicio).
    tarea = None
    if settings.reminder_scheduler:
        tarea = asyncio.create_task(reminder_loop())
        logger.info("Barrida de recordatorios activada (cada hora).")

    yield

    if tarea:
        tarea.cancel()
        with suppress(asyncio.CancelledError):
            await tarea


app = FastAPI(
    lifespan=lifespan,
    title=f"Turnos - {settings.company_name}",
    description="Sistema de turnos para abastecimiento de combustible aeronáutico.",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url=None,
)

# Cookie de sesión firmada. https_only en producción para que no viaje en claro.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="turnos_session",
    max_age=60 * 60 * 24 * 14,  # 14 días
    same_site="lax",
    https_only=settings.is_production,
)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(public.router)
app.include_router(auth_routes.router)
app.include_router(mis_aeronaves.router)
app.include_router(bookings.router)
app.include_router(admin.router)
app.include_router(users_admin.router)
app.include_router(coord.router)
app.include_router(maestros.router)
app.include_router(operador.router)
app.include_router(external_ypf.router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Las llamadas a /api responden JSON; el resto, una página de error."""
    path = request.url.path
    wants_json = (
        path.startswith("/api")
        or path.startswith("/external/")
        or path.startswith("/coord/")
        or path.startswith("/operador/")
        or path.startswith("/admin/abastecedoras")
        or path.startswith("/coord/maestros")
    )
    if wants_json:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    return templates.TemplateResponse(
        request,
        "error.html",
        {"status_code": exc.status_code, "detail": exc.detail, "user": None},
        status_code=exc.status_code,
    )


@app.get("/health", include_in_schema=False)
def health():
    """Chequeo de salud para el monitor de Render."""
    return {"status": "ok"}
