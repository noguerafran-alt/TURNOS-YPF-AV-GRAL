"""Envío de recordatorios de turnos próximos.

Vive acá (y no solo en el script de línea de comandos) porque con SQLite sobre un
disco de Render **no se puede usar un Cron Job aparte**: un disco se monta en un
único servicio, así que un segundo servicio no vería la base. La barrida corre
dentro de la propia app, en una tarea de fondo.

Es idempotente: marca `reminder_sent_at`, así ejecutarla de más nunca manda dos
veces el mismo aviso.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import SessionLocal
from app.emails import booking_payload, send_reminder
from app.models import Booking, BookingStatus

logger = logging.getLogger("turnos.reminders")


def send_due_reminders(*, window_hours: int = 2, dry_run: bool = False) -> tuple[int, int]:
    """Manda los recordatorios que corresponden ahora.

    Busca turnos que arrancan en [ahora + REMINDER_HOURS_BEFORE, + window_hours).
    La ventana es de 2 h por defecto y la barrida corre cada hora: así un
    reinicio del servicio en el medio no deja a nadie sin aviso.

    Devuelve (enviados, fallidos).
    """
    hours = settings.reminder_hours_before
    now = datetime.now(UTC)
    window_start = now + timedelta(hours=hours)
    window_end = window_start + timedelta(hours=window_hours)

    sent = failed = 0

    with SessionLocal() as db:
        bookings = db.scalars(
            select(Booking)
            .options(selectinload(Booking.agenda), selectinload(Booking.user))
            .where(
                Booking.status == BookingStatus.CONFIRMED,
                Booking.reminder_sent_at.is_(None),
                Booking.starts_at >= window_start,
                Booking.starts_at < window_end,
            )
            .order_by(Booking.starts_at)
        ).all()

        if bookings:
            logger.info("Turnos a recordar: %s", len(bookings))

        for booking in bookings:
            label = (
                f"#{booking.id} {booking.user.email} "
                f"{booking.starts_at.astimezone(settings.tz).strftime('%d/%m %H:%M')}"
            )

            if dry_run:
                logger.info("[dry-run] mandaría recordatorio: %s", label)
                continue

            if send_reminder(booking_payload(booking, booking.agenda, booking.user), hours):
                booking.reminder_sent_at = datetime.now(UTC)
                sent += 1
                logger.info("Recordatorio enviado: %s", label)
            else:
                # Sin marcar: la próxima barrida lo reintenta mientras el turno
                # siga dentro de la ventana.
                failed += 1
                logger.warning("No se pudo enviar el recordatorio: %s", label)

        if not dry_run:
            db.commit()

    return sent, failed


async def reminder_loop(interval_minutes: int = 60) -> None:
    """Tarea de fondo: barre los recordatorios cada hora.

    El trabajo con la base es síncrono, así que va a un hilo aparte para no
    bloquear el event loop mientras se atienden requests.
    """
    # Un respiro inicial: durante un deploy conviene que la app empiece a
    # responder antes de ponerse a mandar mails.
    await asyncio.sleep(60)

    while True:
        try:
            sent, failed = await asyncio.to_thread(send_due_reminders)
            if sent or failed:
                logger.info("Barrida de recordatorios: %s enviados, %s fallidos", sent, failed)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — la tarea no puede morir por un error puntual
            logger.exception("Error en la barrida de recordatorios; se reintenta después")

        await asyncio.sleep(interval_minutes * 60)
