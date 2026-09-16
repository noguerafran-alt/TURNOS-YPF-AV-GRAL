"""API externa READ-ONLY para consumo YPF (fase 1).

Prefijo: /external/v1
Auth: X-API-Key o Authorization: Bearer (YPF_API_KEY / EXTERNAL_API_KEY).
Sin POST/PUT/PATCH/DELETE.
"""

from __future__ import annotations

import hmac
from datetime import date, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.database import get_db
from app.matricula import grados_compatibles, lookup_matricula
from app.models import (
    WEEKDAYS,
    Abastecedora,
    Agenda,
    Booking,
    BookingStatus,
    CoordinacionStatus,
    Operador,
)
from app.routers.coord import (
    ACTIVE_COORD,
    _booking_json,
    _day_bounds_utc,
    _get_booking,
)
from app.slots import build_week, week_start

router = APIRouter(prefix="/external/v1", tags=["ypf-external"])


def _configured_api_key() -> str | None:
    key = (settings.ypf_api_key or settings.external_api_key or "").strip()
    return key or None


def _extract_api_key(
    request: Request,
    x_api_key: str | None,
    authorization: str | None,
) -> str | None:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization:
        parts = authorization.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
    # Fallback: algunos clientes mandan solo el token en Authorization
    raw = request.headers.get("Authorization")
    if raw and " " not in raw.strip():
        return raw.strip()
    return None


def _keys_match(provided: str, expected: str) -> bool:
    a, b = provided.encode("utf-8"), expected.encode("utf-8")
    if len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


def require_external_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> None:
    expected = _configured_api_key()
    if expected is None:
        raise HTTPException(
            status_code=503,
            detail="API externa no configurada (faltan YPF_API_KEY / EXTERNAL_API_KEY).",
        )
    provided = _extract_api_key(request, x_api_key, authorization)
    if not provided or not _keys_match(provided, expected):
        raise HTTPException(status_code=401, detail="API key inválida o ausente.")


def _agenda_summary(a: Agenda) -> dict:
    return {
        "id": a.id,
        "slug": a.slug,
        "name": a.name,
        "full_name": a.full_name,
        "product": a.product,
        "location": a.location,
        "address": a.address,
        "is_active": a.is_active,
        "sort_order": a.sort_order,
        "slot_minutes": a.slot_minutes,
        "capacity": a.capacity,
    }


def _slot_rules_summary(a: Agenda) -> list[dict]:
    rules = sorted(a.rules, key=lambda r: (r.weekday, r.start_time))
    return [
        {
            "weekday": r.weekday,
            "weekday_name": WEEKDAYS[r.weekday] if 0 <= r.weekday < 7 else str(r.weekday),
            "start_time": r.start_time.strftime("%H:%M"),
            "end_time": r.end_time.strftime("%H:%M"),
        }
        for r in rules
    ]


def _agenda_detail(a: Agenda) -> dict:
    data = _agenda_summary(a)
    data.update(
        {
            "description": a.description or "",
            "lead_time_hours": a.lead_time_hours,
            "horizon_days": a.horizon_days,
            "cancel_limit_hours": a.cancel_limit_hours,
            "slot_rules_summary": _slot_rules_summary(a),
        }
    )
    return data


@router.get("/health")
def health(_: None = Depends(require_external_api_key)):
    return {"ok": True, "service": "ypf-external", "version": "v1"}


@router.get("/agendas")
def list_agendas(
    active_only: bool = True,
    db: Session = Depends(get_db),
    _: None = Depends(require_external_api_key),
):
    stmt = select(Agenda).order_by(Agenda.sort_order, Agenda.name)
    if active_only:
        stmt = stmt.where(Agenda.is_active.is_(True))
    rows = db.scalars(stmt).all()
    return {"ok": True, "agendas": [_agenda_summary(a) for a in rows]}


@router.get("/agendas/{agenda_id}")
def get_agenda(
    agenda_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_external_api_key),
):
    agenda = db.scalar(
        select(Agenda)
        .options(selectinload(Agenda.rules))
        .where(Agenda.id == agenda_id)
    )
    if agenda is None:
        raise HTTPException(status_code=404, detail="Agenda inexistente.")
    return {"ok": True, "agenda": _agenda_detail(agenda)}


@router.get("/agendas/{agenda_id}/horarios")
def agenda_horarios(
    agenda_id: int,
    date_str: str | None = Query(None, alias="date"),
    db: Session = Depends(get_db),
    _: None = Depends(require_external_api_key),
):
    """Contexto de grilla para un día (slots calculados al vuelo)."""
    agenda = db.get(Agenda, agenda_id)
    if agenda is None:
        raise HTTPException(status_code=404, detail="Agenda inexistente.")
    try:
        day = date.fromisoformat(date_str) if date_str else datetime.now(settings.tz).date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Fecha inválida.") from exc

    monday = week_start(day)
    week = build_week(db, agenda, monday, user_id=None, include_past_days=True)
    day_slots = next((d for d in week if d.day == day), None)
    slots = day_slots.slots if day_slots else []
    return {
        "ok": True,
        "agenda_id": agenda.id,
        "date": day.isoformat(),
        "slot_minutes": agenda.slot_minutes,
        "capacity": agenda.capacity,
        "slots": [
            {
                "starts_at": s.starts_at.isoformat(),
                "ends_at": s.ends_at.isoformat(),
                "hora": s.label,
                "status": s.status,
                "free_places": s.free_places,
            }
            for s in slots
        ],
    }


@router.get("/abastecedoras")
def list_abastecedoras(
    agenda_id: int | None = None,
    grado: str | None = None,
    day: str | None = None,
    db: Session = Depends(get_db),
    _: None = Depends(require_external_api_key),
):
    stmt = select(Abastecedora).where(Abastecedora.activo.is_(True)).order_by(
        Abastecedora.sort_order, Abastecedora.nombre
    )
    if agenda_id:
        stmt = stmt.where(
            or_(Abastecedora.agenda_id.is_(None), Abastecedora.agenda_id == agenda_id)
        )
    rows = db.scalars(stmt).all()
    try:
        ref_day = date.fromisoformat(day) if day else datetime.now(settings.tz).date()
    except ValueError:
        ref_day = datetime.now(settings.tz).date()

    out = []
    for a in rows:
        grado_ok = True if not grado else grados_compatibles(a.grado, grado)
        en_taller = bool(a.fuera_de_servicio_hasta and a.fuera_de_servicio_hasta >= ref_day)
        out.append(
            {
                "id": a.id,
                "nombre": a.nombre,
                "codigo": a.codigo,
                "grado": a.grado,
                "capacidad_l": a.capacidad_l,
                "agenda_id": a.agenda_id,
                "grado_ok": grado_ok,
                "en_taller_hoy": bool(
                    en_taller and a.fuera_de_servicio_hasta == ref_day
                )
                if a.fuera_de_servicio_hasta
                else False,
                "fuera_de_servicio_hasta": a.fuera_de_servicio_hasta.isoformat()
                if a.fuera_de_servicio_hasta
                else None,
                "disabled_today": bool(
                    a.fuera_de_servicio_hasta and a.fuera_de_servicio_hasta >= ref_day
                ),
            }
        )
    return {"ok": True, "abastecedoras": out}


@router.get("/operadores")
def list_operadores(
    db: Session = Depends(get_db),
    _: None = Depends(require_external_api_key),
):
    """Maestro de operadores asignables (Turnera). Sin secretos."""
    rows = db.scalars(
        select(Operador).where(Operador.activo.is_(True)).order_by(Operador.nombre)
    ).all()
    return {
        "ok": True,
        "operadores": [
            {
                "id": o.id,
                "name": o.nombre,
                "user_id": o.user_id,
                "activo": o.activo,
            }
            for o in rows
        ],
    }


@router.get("/board")
def board(
    agenda_id: int | None = None,
    date_str: str | None = Query(None, alias="date"),
    status_filter: str | None = Query(None, alias="status"),
    q: str = "",
    db: Session = Depends(get_db),
    _: None = Depends(require_external_api_key),
):
    """Mismo shape que /coord/board (KPIs + bookings del día)."""
    try:
        day = date.fromisoformat(date_str) if date_str else datetime.now(settings.tz).date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Fecha inválida.") from exc

    day_from, day_to = _day_bounds_utc(day)

    stmt = (
        select(Booking)
        .options(
            selectinload(Booking.agenda),
            selectinload(Booking.user),
            selectinload(Booking.abastecedora),
            selectinload(Booking.operador),
        )
        .where(
            Booking.status == BookingStatus.CONFIRMED,
            Booking.coordinacion_status.in_(ACTIVE_COORD),
            Booking.starts_at >= day_from,
            Booking.starts_at < day_to,
        )
        .order_by(Booking.starts_at)
    )
    if agenda_id:
        stmt = stmt.where(Booking.agenda_id == agenda_id)
    if status_filter and status_filter.upper() in ACTIVE_COORD:
        stmt = stmt.where(Booking.coordinacion_status == status_filter.upper())
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(Booking.aircraft.ilike(like), Booking.combustible_declarado.ilike(like))
        )

    rows = db.scalars(stmt).all()
    items = [_booking_json(b) for b in rows]

    base = select(Booking).where(
        Booking.status == BookingStatus.CONFIRMED,
        Booking.coordinacion_status.in_(ACTIVE_COORD),
        Booking.starts_at >= day_from,
        Booking.starts_at < day_to,
    )
    if agenda_id:
        base = base.where(Booking.agenda_id == agenda_id)
    all_day = db.scalars(base).all()

    def _count(st: str) -> int:
        return sum(1 for b in all_day if b.coordinacion_status == st)

    kpis = {
        "pendientes": _count(CoordinacionStatus.PENDIENTE),
        "programados": _count(CoordinacionStatus.PROGRAMADO),
        "abastecidos": _count(CoordinacionStatus.ABASTECIDO),
        "ausentes": _count(CoordinacionStatus.AUSENTE),
        "primera_carga": sum(1 for b in all_day if b.primera_carga or b.unknown_matricula),
    }

    return {
        "ok": True,
        "date": day.isoformat(),
        "agenda_id": agenda_id,
        "kpis": kpis,
        "bookings": items,
    }


@router.get("/bookings/{booking_id}")
def get_booking(
    booking_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_external_api_key),
):
    booking = _get_booking(db, booking_id)
    return {"ok": True, "booking": _booking_json(booking)}


@router.get("/matriculas/{matricula}")
def get_matricula(
    matricula: str,
    db: Session = Depends(get_db),
    _: None = Depends(require_external_api_key),
):
    """Stub de lookup (mismo shape que /api/matricula/{matricula})."""
    return lookup_matricula(db, matricula).as_dict()
