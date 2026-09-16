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
from app.empresa_service import flag_matricula_otra_empresa
from app.matricula import lookup_matricula
from app.models import Agenda, Booking, BookingStatus, CoordinacionStatus, OrigenBooking, User
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
    # Matrícula, modelo y litros son obligatorios: son los datos mínimos que
    # necesita el operador para preparar el abastecimiento. El número de vuelo
    # queda opcional porque no todas las aeronaves que cargan combustible acá
    # vuelan un tramo comercial con código asignado.
    aircraft: str = Field(min_length=1, max_length=40)
    aircraft_model: str = Field(min_length=1, max_length=60)
    liters: int = Field(ge=1, le=20_000)
    flight_number: str = Field(default="", max_length=20)
    notes: str = Field(default="", max_length=500)

    @field_validator("aircraft", "aircraft_model")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Este campo es obligatorio.")
        return value

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
    # Gate de perfil completo. La UI ya evita llegar hasta acá sin teléfono y
    # empresa (redirige a /perfil antes de abrir el diálogo), pero esto es lo
    # que realmente lo garantiza: nada impide pegarle a la API directo.
    # 403, no 409: es un problema de permiso/estado de la cuenta, no del turno.
    if not user.phone.strip() or not user.company.strip():
        raise HTTPException(
            status_code=403,
            detail="Completá tu perfil (teléfono y empresa) antes de reservar un turno.",
        )

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

        aircraft = payload.aircraft.strip().upper()[:40]
        lookup = lookup_matricula(db, aircraft)
        combustible = lookup.combustible or agenda.product or ""
        otra_empresa = flag_matricula_otra_empresa(db, user=user, raw_matricula=aircraft)

        booking = Booking(
            agenda_id=agenda.id,
            user_id=user.id,
            starts_at=slot.starts_at,
            ends_at=slot.ends_at,
            status=BookingStatus.CONFIRMED,
            aircraft=aircraft,
            aircraft_model=payload.aircraft_model.strip()[:60],
            liters=payload.liters,
            flight_number=payload.flight_number.strip().upper()[:20],
            notes=payload.notes.strip()[:500],
            origen=OrigenBooking.WEB,
            coordinacion_status=CoordinacionStatus.PENDIENTE,
            combustible_declarado=combustible,
            primera_carga=lookup.primera_carga,
            unknown_matricula=lookup.unknown_matricula,
            matricula_otra_empresa=otra_empresa,
            sobreturno=False,
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
    # Mantener pipeline operativo alineado
    if booking.coordinacion_status not in (
        CoordinacionStatus.ABASTECIDO,
        CoordinacionStatus.AUSENTE,
        CoordinacionStatus.CANCELADO,
    ):
        booking.coordinacion_status = CoordinacionStatus.CANCELADO
        booking.cancelado_coord_at = now
        if not booking.cancelado_motivo:
            booking.cancelado_motivo = "Cancelado desde reserva" if not by_admin else "Cancelado por administración"
    db.commit()

    # Solo se avisa de turnos futuros: no tiene sentido notificar una limpieza
    # administrativa de turnos que ya pasaron.
    owner = db.get(User, booking.user_id)
    if agenda and owner and booking.starts_at > now:
        background.add_task(
            send_cancellation, booking_payload(booking, agenda, owner), by_admin=by_admin
        )

    return {"ok": True, "message": "Turno cancelado. El horario vuelve a quedar disponible."}
