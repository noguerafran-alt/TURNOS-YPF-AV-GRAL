"""Generación de horarios disponibles.

Los slots no viven en la base: se calculan al vuelo para la semana pedida.

    ScheduleRule (franjas semanales)
        - Closure   (cortes puntuales)
        - Booking   (turnos ya tomados, contra la capacidad de la agenda)
        - pasado / anticipación mínima / horizonte de reserva
        = grilla de horarios que ve el cliente

Todo se calcula en hora local de la agenda y se guarda/compara en UTC.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Agenda, Booking, BookingStatus, Closure, ScheduleRule


class SlotStatus(StrEnum):
    FREE = "free"          # se puede reservar
    TAKEN = "taken"        # sin cupo (lo tomó otro cliente)
    MINE = "mine"          # el usuario actual ya tiene este turno
    CLOSED = "closed"      # cortado por un Closure
    PAST = "past"          # ya pasó, o no respeta la anticipación mínima


@dataclass(slots=True)
class Slot:
    starts_at: datetime          # UTC
    ends_at: datetime            # UTC
    status: SlotStatus
    booking_id: int | None = None
    free_places: int = 0

    @property
    def local_start(self) -> datetime:
        return self.starts_at.astimezone(settings.tz)

    @property
    def label(self) -> str:
        """Etiqueta del botón: 15:40"""
        return self.local_start.strftime("%H:%M")

    @property
    def is_bookable(self) -> bool:
        return self.status == SlotStatus.FREE


@dataclass(slots=True)
class DaySlots:
    day: date
    slots: list[Slot]

    @property
    def has_service(self) -> bool:
        """False si ese día directamente no se atiende (no se dibuja la fila)."""
        return bool(self.slots)

    @property
    def free_count(self) -> int:
        return sum(1 for s in self.slots if s.status == SlotStatus.FREE)


def week_start(day: date) -> date:
    """Lunes de la semana que contiene a `day`."""
    return day - timedelta(days=day.weekday())


def local_dt(day: date, t) -> datetime:
    """Combina fecha + hora en la zona horaria de la agenda."""
    return datetime.combine(day, t, tzinfo=settings.tz)


def _rule_end(day: date, rule: ScheduleRule) -> datetime:
    """Fin de la franja. Si end <= start se asume que cruza la medianoche."""
    start = local_dt(day, rule.start_time)
    end = local_dt(day, rule.end_time)
    if end <= start:
        end += timedelta(days=1)
    return end


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def build_week(
    db: Session,
    agenda: Agenda,
    monday: date,
    *,
    user_id: int | None = None,
    now: datetime | None = None,
    include_past_days: bool = True,
) -> list[DaySlots]:
    """Devuelve los 7 días de la semana que arranca en `monday`."""
    now = now or datetime.now(UTC)

    week_from = local_dt(monday, datetime.min.time()).astimezone(UTC)
    week_to = (local_dt(monday + timedelta(days=8), datetime.min.time())).astimezone(UTC)

    rules = db.scalars(
        select(ScheduleRule).where(ScheduleRule.agenda_id == agenda.id)
    ).all()

    closures = db.scalars(
        select(Closure).where(
            Closure.agenda_id == agenda.id,
            Closure.ends_at > week_from,
            Closure.starts_at < week_to,
        )
    ).all()

    bookings = db.scalars(
        select(Booking).where(
            Booking.agenda_id == agenda.id,
            Booking.status == BookingStatus.CONFIRMED,
            Booking.starts_at >= week_from,
            Booking.starts_at < week_to,
        )
    ).all()

    # Ocupación por horario y turnos propios del usuario
    taken: dict[datetime, int] = {}
    mine: dict[datetime, int] = {}
    for booking in bookings:
        key = booking.starts_at.astimezone(UTC)
        taken[key] = taken.get(key, 0) + 1
        if user_id is not None and booking.user_id == user_id:
            mine[key] = booking.id

    # Ventana en la que se acepta reservar
    bookable_from = now + timedelta(hours=agenda.lead_time_hours)
    bookable_to = now + timedelta(days=agenda.horizon_days)

    week: list[DaySlots] = []
    for offset in range(7):
        day = monday + timedelta(days=offset)
        day_rules = [r for r in rules if r.weekday == day.weekday()]
        slots: list[Slot] = []

        for rule in sorted(day_rules, key=lambda r: r.start_time):
            cursor = local_dt(day, rule.start_time)
            limit = _rule_end(day, rule)
            step = timedelta(minutes=agenda.slot_minutes)

            while cursor + step <= limit:
                starts_utc = cursor.astimezone(UTC)
                ends_utc = (cursor + step).astimezone(UTC)
                cursor += step

                if any(_overlaps(starts_utc, ends_utc, c.starts_at, c.ends_at) for c in closures):
                    status = SlotStatus.CLOSED
                elif starts_utc in mine:
                    status = SlotStatus.MINE
                elif starts_utc < bookable_from or starts_utc > bookable_to:
                    status = SlotStatus.PAST
                elif taken.get(starts_utc, 0) >= agenda.capacity:
                    status = SlotStatus.TAKEN
                else:
                    status = SlotStatus.FREE

                slots.append(
                    Slot(
                        starts_at=starts_utc,
                        ends_at=ends_utc,
                        status=status,
                        booking_id=mine.get(starts_utc),
                        free_places=max(0, agenda.capacity - taken.get(starts_utc, 0)),
                    )
                )

        if not include_past_days and slots and all(s.status == SlotStatus.PAST for s in slots):
            slots = []

        week.append(DaySlots(day=day, slots=sorted(slots, key=lambda s: s.starts_at)))

    return week


def find_slot(
    db: Session,
    agenda: Agenda,
    starts_at: datetime,
    *,
    user_id: int | None = None,
    now: datetime | None = None,
) -> Slot | None:
    """Busca un horario puntual dentro de su semana.

    Se usa al reservar: garantiza que el horario que mandó el cliente existe de
    verdad en la grilla y no es una fecha inventada a mano contra la API.
    """
    starts_at = starts_at.astimezone(UTC)
    local_day = starts_at.astimezone(settings.tz).date()

    for day_slots in build_week(
        db, agenda, week_start(local_day), user_id=user_id, now=now
    ):
        for slot in day_slots.slots:
            if slot.starts_at == starts_at:
                return slot
    return None
