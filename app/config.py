"""Configuración de la app, leída de variables de entorno (.env en local)."""

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# encoding explícito: sin esto, en Windows el .env se lee con la codificación
# local (cp1252) y los acentos salen rotos ("aeronÃ¡utico" en vez de "aeronáutico").
load_dotenv(encoding="utf-8")


def _normalize_db_url(url: str) -> str:
    """Render entrega la URL como postgres://; SQLAlchemy 2 necesita postgresql+psycopg://."""
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


class Settings:
    def __init__(self) -> None:
        # --- Base de datos ---
        # Sin DATABASE_URL cae en SQLite, así podés probar sin instalar Postgres.
        self.database_url: str = _normalize_db_url(
            os.getenv("DATABASE_URL", "sqlite:///./turnos.db")
        )

        # --- Sesiones ---
        # En producción SIEMPRE definir SECRET_KEY (Render la genera sola con el render.yaml).
        self.secret_key: str = os.getenv("SECRET_KEY", "dev-secret-no-usar-en-produccion")

        # --- Google OAuth ---
        # Credenciales de https://console.cloud.google.com/apis/credentials
        self.google_client_id: str = os.getenv("GOOGLE_CLIENT_ID", "")
        self.google_client_secret: str = os.getenv("GOOGLE_CLIENT_SECRET", "")

        # --- Login de desarrollo ---
        # Permite entrar con un email cualquiera SIN Google. Solo para probar en local:
        # el arranque falla si queda activo con ENVIRONMENT=production.
        self.dev_login: bool = _as_bool(os.getenv("DEV_LOGIN"), default=False)
        self.environment: str = os.getenv("ENVIRONMENT", "development").lower()

        # --- Administradores ---
        # Lista separada por comas de los emails que ven el panel /admin.
        self.admin_emails: set[str] = {
            email.strip().lower()
            for email in os.getenv("ADMIN_EMAILS", "").split(",")
            if email.strip()
        }

        # --- Empresa (MVP de una sola empresa) ---
        self.company_name: str = os.getenv("COMPANY_NAME", "Aeroplantas YPF")
        self.company_tagline: str = os.getenv(
            "COMPANY_TAGLINE", "Turnos de abastecimiento de combustible aeronáutico"
        )
        self.support_email: str = os.getenv("SUPPORT_EMAIL", "turnos@tudominio.com")

        # --- Emails (Resend) ---
        # Sin RESEND_API_KEY, los emails se guardan en la carpeta outbox/ en vez
        # de enviarse: sirve para desarrollar y testear el contenido.
        self.resend_api_key: str = os.getenv("RESEND_API_KEY", "")
        # El dominio del remitente tiene que estar verificado en Resend.
        self.email_from: str = os.getenv("EMAIL_FROM", "Turnos <onboarding@resend.dev>")
        self.email_enabled: bool = _as_bool(os.getenv("EMAIL_ENABLED"), default=True)
        # Cuántas horas antes del turno se manda el recordatorio.
        self.reminder_hours_before: int = int(os.getenv("REMINDER_HOURS_BEFORE", "24"))
        # Barrida de recordatorios dentro de la app, cada hora. Apagala solo si
        # vas a dispararlos desde afuera (send_reminders.py en un cron externo).
        self.reminder_scheduler: bool = _as_bool(os.getenv("REMINDER_SCHEDULER"), default=True)

        # --- Reglas de negocio ---
        self.timezone_name: str = os.getenv("TIMEZONE", "America/Argentina/Buenos_Aires")
        # Máximo de turnos futuros que puede tener un cliente por agenda.
        self.max_open_bookings: int = int(os.getenv("MAX_OPEN_BOOKINGS", "3"))

        self.base_url: str = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")

        # Aplicar las migraciones al arrancar. Apagalo solo si vas a correr
        # `alembic upgrade head` por separado (ej: varias instancias en paralelo).
        self.run_migrations: bool = _as_bool(os.getenv("RUN_MIGRATIONS"), default=True)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    def validate(self) -> None:
        """Chequeos de arranque: mejor fallar fuerte que quedar inseguro en producción."""
        if not self.is_production:
            return

        problems: list[str] = []
        if self.secret_key == "dev-secret-no-usar-en-produccion":
            problems.append("SECRET_KEY no está configurada")
        if self.dev_login:
            problems.append("DEV_LOGIN debe estar apagado en producción")
        if not self.google_enabled:
            problems.append("Faltan GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET")
        if not self.admin_emails:
            problems.append("ADMIN_EMAILS está vacío: nadie podría entrar al panel")
        if self.email_enabled and not self.resend_api_key:
            problems.append(
                "Falta RESEND_API_KEY (o apagá los emails con EMAIL_ENABLED=false). "
                "Sin la clave, los emails se escribirían a disco y se darían por enviados"
            )

        if problems:
            raise RuntimeError(
                "Configuración inválida para producción:\n  - " + "\n  - ".join(problems)
            )


settings = Settings()
