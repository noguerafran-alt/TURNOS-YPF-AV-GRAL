"""Emails transaccionales: confirmación, cancelación y recordatorio.

Proveedor: Resend (https://resend.com) vía su API HTTP, sin SDK.
Si no hay RESEND_API_KEY configurada, los mensajes se guardan como archivos .html
en la carpeta `outbox/` y se loguean por consola. Así se puede desarrollar y testear
el contenido sin mandar correo de verdad ni depender de una cuenta.

Los envíos se hacen en background (BackgroundTasks): que el proveedor esté lento
o caído no puede demorar ni romper la reserva del cliente.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.templating import fmt_date_long, fmt_time, templates

logger = logging.getLogger("turnos.emails")

RESEND_ENDPOINT = "https://api.resend.com/emails"
OUTBOX = Path(__file__).resolve().parent.parent / "outbox"


# ============================================================
# Datos del turno para la plantilla
# ============================================================
def booking_payload(booking, agenda, user) -> dict[str, Any]:
    """Aplana el turno a valores simples.

    El envío ocurre después de cerrada la sesión de base de datos, así que no se
    pueden pasar objetos del ORM: quedarían desconectados y explotarían al leer
    una relación.
    """
    return {
        "booking_id": booking.id,
        "email": user.email,
        "user_name": user.display_name,
        "agenda_name": agenda.full_name,
        "location": agenda.location,
        "address": agenda.address,
        "important_info": agenda.important_info,
        "cancel_limit_hours": agenda.cancel_limit_hours,
        "date_long": fmt_date_long(booking.starts_at),
        "time": fmt_time(booking.starts_at),
        "aircraft": booking.aircraft,
        "aircraft_model": booking.aircraft_model,
        "flight_number": booking.flight_number,
        "liters": booking.liters,
        "notes": booking.notes,
        "combustible_declarado": getattr(booking, "combustible_declarado", None)
            or getattr(agenda, "product", None)
            or "",
        "bookings_url": f"{settings.base_url}/mis-turnos",
        "agenda_url": f"{settings.base_url}/a/{agenda.slug}",
    }


# ============================================================
# Envío
# ============================================================
def _html_to_text(html: str) -> str:
    """Versión en texto plano para clientes que no renderizan HTML."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|h1|h2|h3|div|tr|li)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def _save_to_outbox(to: str, subject: str, html: str) -> None:
    """Modo desarrollo: deja el email en disco en vez de enviarlo."""
    OUTBOX.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_to = re.sub(r"[^a-zA-Z0-9._-]", "_", to)
    path = OUTBOX / f"{stamp}_{safe_to}.html"
    path.write_text(
        f"<!-- Para: {to} -->\n<!-- Asunto: {subject} -->\n{html}", encoding="utf-8"
    )
    logger.info("Email guardado en outbox (sin enviar): %s -> %s", subject, path.name)


def send_email(to: str, subject: str, html: str) -> bool:
    """Manda un email. Devuelve True si se entregó al proveedor."""
    if not settings.email_enabled:
        logger.info("Emails deshabilitados (EMAIL_ENABLED=false): se omite '%s'", subject)
        return False

    if not settings.resend_api_key:
        # Transporte de desarrollo: se considera "entregado" para que el cron de
        # recordatorios marque el turno y no reintente en loop. En producción no
        # puede pasar: config.validate() exige la API key si los emails están activos.
        _save_to_outbox(to, subject, html)
        return True

    try:
        response = httpx.post(
            RESEND_ENDPOINT,
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.email_from,
                "to": [to],
                "subject": subject,
                "html": html,
                "text": _html_to_text(html),
            },
            timeout=15,
        )
        if response.status_code >= 400:
            logger.error("Resend rechazó el email (%s): %s", response.status_code, response.text[:300])
            return False

        logger.info("Email enviado a %s: %s", to, subject)
        return True
    except Exception as exc:  # noqa: BLE001 — un email caído nunca debe romper la app
        logger.exception("Fallo al enviar el email a %s: %s", to, exc)
        return False


def _render(template: str, data: dict[str, Any]) -> str:
    return templates.get_template(f"emails/{template}").render(
        **data,
        company_name=settings.company_name,
        support_email=settings.support_email,
        base_url=settings.base_url,
    )


# ============================================================
# Los tres emails del sistema
# ============================================================
def send_confirmation(data: dict[str, Any]) -> bool:
    subject = f"Turno confirmado: {data['agenda_name']} — {data['date_long']}, {data['time']}"
    return send_email(data["email"], subject, _render("confirmation.html", data))


def send_cancellation(
    data: dict[str, Any],
    *,
    by_admin: bool = False,
    reason: str | None = None,
) -> bool:
    """reason opcionales: 'cambio_grado' (cancela por cambio de grado de matrícula)."""
    if reason == "cambio_grado":
        prefix = "Turno cancelado por cambio de grado"
    elif by_admin:
        prefix = "Turno cancelado por la aeroplanta"
    else:
        prefix = "Turno cancelado"
    subject = f"{prefix}: {data['agenda_name']} — {data['date_long']}, {data['time']}"
    return send_email(
        data["email"],
        subject,
        _render(
            "cancellation.html",
            {**data, "by_admin": by_admin, "reason": reason},
        ),
    )


def send_reminder(data: dict[str, Any], hours_before: int) -> bool:
    subject = f"Recordatorio: tu turno es {data['date_long']} a las {data['time']}"
    return send_email(
        data["email"], subject, _render("reminder.html", {**data, "hours_before": hours_before})
    )
