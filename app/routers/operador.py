"""Panel operador de planta: turnos PROGRAMADOS asignados a él."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth import require_operador
from app.config import settings
from app.database import get_db
from app.matricula import upsert_matricula_combustible
from app.models import Booking, BookingStatus, CoordinacionStatus, Operador, User
from app.templating import templates

router = APIRouter(tags=["operador"])


def _day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    start_local = datetime.combine(day, time.min, tzinfo=settings.tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _booking_json(b: Booking) -> dict:
    local = b.starts_at.astimezone(settings.tz)
    ab = b.abastecedora
    user = b.user
    return {
        "id": b.id,
        "starts_at": b.starts_at.isoformat(),
        "hora": local.strftime("%H:%M"),
        "fecha": local.date().isoformat(),
        "aircraft": b.aircraft,
        "aircraft_model": b.aircraft_model,
        "liters": b.liters,
        "notes": b.notes,
        "coordinacion_status": b.coordinacion_status,
        "combustible_declarado": b.combustible_declarado,
        "primera_carga": b.primera_carga,
        "unknown_matricula": b.unknown_matricula,
        "matricula_otra_empresa": bool(getattr(b, "matricula_otra_empresa", False)),
        "badge_primera_carga": bool(b.primera_carga or b.unknown_matricula),
        "combustible_reconfirmado_en_persona": b.combustible_reconfirmado_en_persona,
        "reconfirm_pregunte_en_persona": b.reconfirm_pregunte_en_persona,
        "reconfirm_coincide_declarado": b.reconfirm_coincide_declarado,
        "cliente": user.display_name if user else "",
        "empresa": ((user.empresa_nombre if user else "") or (user.company if user else "") or ""),
        "agenda_id": b.agenda_id,
        "agenda_name": b.agenda.full_name if b.agenda else "",
        "abastecedora_id": b.abastecedora_id,
        "abastecedora": ab.nombre if ab else None,
        "operador_id": b.operador_id,
        "operador_user_id": b.operador_user_id,
        "ausente_motivo": b.ausente_motivo,
    }


def _assigned_to_user(booking: Booking, op: User) -> bool:
    """Asignación vía maestro.user_id o legacy operador_user_id."""
    if booking.operador_user_id == op.id:
        return True
    maestro = booking.operador
    if maestro is not None and maestro.user_id == op.id:
        return True
    return False


def _get_assigned(db: Session, booking_id: int, op: User) -> Booking:
    booking = db.scalar(
        select(Booking)
        .options(
            selectinload(Booking.agenda),
            selectinload(Booking.abastecedora),
            selectinload(Booking.user),
            selectinload(Booking.operador),
        )
        .where(Booking.id == booking_id)
    )
    if booking is None:
        raise HTTPException(status_code=404, detail="No encontramos ese turno.")
    if not _assigned_to_user(booking, op):
        raise HTTPException(status_code=403, detail="Ese turno no está asignado a vos.")
    return booking


def _require_active_programado(booking: Booking) -> None:
    if booking.status != BookingStatus.CONFIRMED:
        raise HTTPException(status_code=409, detail="Ese turno no está confirmado.")
    if booking.coordinacion_status != CoordinacionStatus.PROGRAMADO:
        raise HTTPException(
            status_code=409,
            detail="Solo un turno programado puede actualizarse desde el panel operador.",
        )


class ReconfirmarBody(BaseModel):
    reconfirm_pregunte_en_persona: bool = False
    reconfirm_coincide_declarado: bool = False
    combustible_reconfirmado_en_persona: bool = False
    combustible: str | None = None


class MotivoBody(BaseModel):
    motivo: str = ""


@router.get("/operador")
def operador_panel(
    request: Request,
    op: User = Depends(require_operador),
):
    today = datetime.now(settings.tz).date()
    return templates.TemplateResponse(
        request,
        "operador/panel.html",
        {"user": op, "today": today.isoformat()},
    )


@router.get("/operador/board")
def operador_board(
    date_str: str | None = Query(None, alias="date"),
    db: Session = Depends(get_db),
    op: User = Depends(require_operador),
):
    try:
        day = date.fromisoformat(date_str) if date_str else datetime.now(settings.tz).date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Fecha inválida.") from exc

    day_from, day_to = _day_bounds_utc(day)
    assign_filter = Booking.operador_user_id == op.id
    maestro_ids = list(
        db.scalars(select(Operador.id).where(Operador.user_id == op.id)).all()
    )
    if maestro_ids:
        assign_filter = or_(assign_filter, Booking.operador_id.in_(maestro_ids))

    rows = db.scalars(
        select(Booking)
        .options(
            selectinload(Booking.agenda),
            selectinload(Booking.abastecedora),
            selectinload(Booking.operador),
        )
        .where(
            Booking.status == BookingStatus.CONFIRMED,
            Booking.coordinacion_status == CoordinacionStatus.PROGRAMADO,
            assign_filter,
            Booking.starts_at >= day_from,
            Booking.starts_at < day_to,
        )
        .order_by(Booking.starts_at)
    ).all()
    return {
        "ok": True,
        "date": day.isoformat(),
        "bookings": [_booking_json(b) for b in rows],
        "count": len(rows),
    }


@router.post("/operador/bookings/{booking_id}/reconfirmar")
def reconfirmar(
    booking_id: int,
    body: ReconfirmarBody,
    db: Session = Depends(get_db),
    op: User = Depends(require_operador),
):
    booking = _get_assigned(db, booking_id, op)
    _require_active_programado(booking)
    booking.reconfirm_pregunte_en_persona = body.reconfirm_pregunte_en_persona
    booking.reconfirm_coincide_declarado = body.reconfirm_coincide_declarado
    booking.combustible_reconfirmado_en_persona = body.combustible_reconfirmado_en_persona
    if body.combustible and body.combustible.strip():
        booking.combustible_declarado = body.combustible.strip()
    if body.combustible_reconfirmado_en_persona:
        booking.reconfirmado_at = datetime.now(UTC)
        booking.reconfirmado_by = op.id
    db.commit()
    booking = _get_assigned(db, booking_id, op)
    return {"ok": True, "booking": _booking_json(booking)}


@router.post("/operador/bookings/{booking_id}/abastecer")
def abastecer(
    booking_id: int,
    db: Session = Depends(get_db),
    op: User = Depends(require_operador),
):
    booking = _get_assigned(db, booking_id, op)
    _require_active_programado(booking)

    needs_check = booking.primera_carga or booking.unknown_matricula
    if needs_check:
        if not (
            booking.reconfirm_pregunte_en_persona
            and booking.reconfirm_coincide_declarado
            and booking.combustible_reconfirmado_en_persona
        ):
            raise HTTPException(
                status_code=409,
                detail="Primero reconfirmá el combustible en persona.",
            )

    booking.coordinacion_status = CoordinacionStatus.ABASTECIDO
    booking.abastecido_at = datetime.now(UTC)

    fuel = booking.combustible_declarado or (booking.agenda.product if booking.agenda else "")
    if booking.aircraft and fuel:
        upsert_matricula_combustible(db, raw_matricula=booking.aircraft, combustible=fuel)

    db.commit()
    booking = _get_assigned(db, booking_id, op)
    return {"ok": True, "booking": _booking_json(booking), "message": "Turno marcado como abastecido."}


@router.post("/operador/bookings/{booking_id}/ausente")
def ausente(
    booking_id: int,
    body: MotivoBody,
    db: Session = Depends(get_db),
    op: User = Depends(require_operador),
):
    booking = _get_assigned(db, booking_id, op)
    _require_active_programado(booking)
    booking.coordinacion_status = CoordinacionStatus.AUSENTE
    booking.ausente_at = datetime.now(UTC)
    booking.ausente_motivo = (body.motivo or "").strip()[:300]
    db.commit()
    booking = _get_assigned(db, booking_id, op)
    return {"ok": True, "booking": _booking_json(booking), "message": "Marcado como no se presentó."}
