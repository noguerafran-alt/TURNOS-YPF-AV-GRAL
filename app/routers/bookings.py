"""API de reservas.

El punto delicado es la concurrencia: dos clientes pueden tocar el mismo horario
en el mismo segundo. La reserva se hace dentro de una transacción que primero
bloquea la fila de la agenda (SELECT ... FOR UPDATE), así los pedidos simultáneos
sobre la misma agenda se serializan y el conteo de cupo nunca se pasa.
"""

import threading
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_user
from app.config import settings
from app.database import get_db, is_postgres
from app.emails import booking_payload, send_cancellation, send_confirmation
from app.models import Agenda, Booking, BookingStatus, User
from app.slots import SlotStatus, find_slot

router = APIRouter(prefix="/api", tags=["reservas"])

# SQLite no implementa SELECT ... FOR UPDATE: lo ignora en silencio, así que dos
# pedidos simultáneos podrían pasar los dos el control de cupo. El lock en memoria
# lo resuelve porque la app corre en un único proceso: es exactamente la condición
# que impone el disco de Render (una instancia, un worker). Si algún día se pasa a
# Postgres con varias instancias, ahí manda el bloqueo de fila, que sí funciona
# entre procesos.
_sqlite_booking_lock = threading.Lock()


@contextmanager
def lock_agenda(db: Session, agenda: Agenda):
    """Serializa las reservas de una misma agenda hasta el commit."""
    if is_postgres():
        db.execute(select(Agenda.id).where(Agenda.id == agenda.id).with_for_update())
        yield
    else:
        with _sqlite_booking_lock:
            yield


class BookingRequest(BaseModel):
    slug: str
    starts_at: datetime
    aircraft: str = Field(default="", max_length=40)
    aircraft_model: str = Field(default="", max_length=60)
    liters: int | None = Field(default=None, ge=0, le=200_000)
    notes: str = Field(default="", max_length=500)

    @field_validator("starts_at")
    @classmethod
    def ensure_tz(cls, value: datetime) -> datetime:
        # Un datetime sin zona se interpreta como hora local de la empresa
        if value.tzinfo is None:
            value = value.replace(tzinfo=settings.tz)
        return value.astimezone(UTC)


class BookingResponse(BaseModel):
    id: int
    starts_at: datetime
    ends_at: datetime
    agenda: str
    message: str


@router.post("/bookings", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
def create_booking(
    payload: BookingRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    agenda = db.scalar(select(Agenda).where(Agenda.slug == payload.slug))
    if agenda is None or not agenda.is_active:
        raise HTTPException(status_code=404, detail="Esa agenda no está disponible.")

    # --- Sección crítica: nadie más reserva en esta agenda hasta el commit ---
    with lock_agenda(db, agenda):
        slot = find_slot(db, agenda, payload.starts_at, user_id=user.id)
        if slot is None:
            raise HTTPException(
                status_code=400,
                detail="Ese horario no existe en la agenda. Actualizá la página y probá de nuevo.",
            )

        if slot.status == SlotStatus.MINE:
            raise HTTPException(status_code=409, detail="Ya tenés reservado ese horario.")
        if slot.status == SlotStatus.CLOSED:
            raise HTTPException(
                status_code=409, detail="Ese horario está cerrado por la aeroplanta."
            )
        if slot.status == SlotStatus.PAST:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Ese horario ya no se puede reservar. Se reserva con al menos "
                    f"{agenda.lead_time_hours} h de anticipación."
                ),
            )
        if slot.status == SlotStatus.TAKEN:
            raise HTTPException(
                status_code=409, detail="Alguien tomó ese horario recién. Elegí otro, por favor."
            )

        # Tope de turnos futuros por cliente y por agenda (evita acaparar la grilla)
        open_bookings = db.scalar(
            select(func.count(Booking.id)).where(
                Booking.agenda_id == agenda.id,
                Booking.user_id == user.id,
                Booking.status == BookingStatus.CONFIRMED,
                Booking.starts_at > datetime.now(UTC),
            )
        )
        if open_bookings >= settings.max_open_bookings:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Ya tenés {open_bookings} turnos pendientes en esta agenda "
                    f"(máximo {settings.max_open_bookings}). Cancelá uno para reservar otro."
                ),
            )

        booking = Booking(
            agenda_id=agenda.id,
            user_id=user.id,
            starts_at=slot.starts_at,
            ends_at=slot.ends_at,
            status=BookingStatus.CONFIRMED,
            aircraft=payload.aircraft.strip().upper()[:40],
            aircraft_model=payload.aircraft_model.strip()[:60],
            liters=payload.liters,
            notes=payload.notes.strip()[:500],
        )
        db.add(booking)
        db.commit()
        db.refresh(booking)

    # El email sale después de responder: si el proveedor tarda, el cliente no espera.
    background.add_task(send_confirmation, booking_payload(booking, agenda, user))

    local = booking.starts_at.astimezone(settings.tz)
    return BookingResponse(
        id=booking.id,
        starts_at=booking.starts_at,
        ends_at=booking.ends_at,
        agenda=agenda.full_name,
        message=f"Turno confirmado para el {local.strftime('%d/%m/%Y')} a las {local.strftime('%H:%M')}.",
    )


@router.post("/bookings/{booking_id}/cancel")
def cancel_booking(
    booking_id: int,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    if user is None:
        raise HTTPException(status_code=401, detail="Iniciá sesión para cancelar un turno.")

    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="No encontramos ese turno.")

    if booking.user_id != user.id and not user.is_admin:
        # Mismo mensaje que "no existe": no confirmamos turnos ajenos
        raise HTTPException(status_code=404, detail="No encontramos ese turno.")

    if booking.status == BookingStatus.CANCELLED:
        return {"ok": True, "message": "Ese turno ya estaba cancelado."}

    now = datetime.now(UTC)
    agenda = db.get(Agenda, booking.agenda_id)

    if not user.is_admin:
        limit_hours = agenda.cancel_limit_hours if agenda else 0
        remaining = (booking.starts_at - now).total_seconds() / 3600
        if remaining < limit_hours:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Los turnos se cancelan hasta {limit_hours} h antes. "
                    f"Comunicate con la aeroplanta para reprogramarlo."
                ),
            )

    by_admin = user.is_admin and booking.user_id != user.id

    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = now
    booking.cancelled_by_admin = by_admin
    db.commit()

    # Solo se avisa de turnos futuros: no tiene sentido notificar una limpieza
    # administrativa de turnos que ya pasaron.
    owner = db.get(User, booking.user_id)
    if agenda and owner and booking.starts_at > now:
        background.add_task(
            send_cancellation, booking_payload(booking, agenda, owner), by_admin=by_admin
        )

    return {"ok": True, "message": "Turno cancelado. El horario vuelve a quedar disponible."}
