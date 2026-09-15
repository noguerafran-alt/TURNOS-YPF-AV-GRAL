"""Panel coordinador: tablero del día, asignación, estados operativos."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth import require_admin
from app.config import settings
from app.database import get_db
from app.matricula import (
    grados_compatibles,
    lookup_matricula,
    normalize_grado,
    upsert_matricula_combustible,
)
from app.models import (
    Abastecedora,
    Agenda,
    Booking,
    BookingStatus,
    CoordinacionStatus,
    OrigenBooking,
    Role,
    User,
)
from app.routers.bookings import lock_agenda
from app.slots import SlotStatus, find_slot
from app.templating import templates

router = APIRouter(tags=["coordinacion"])

ACTIVE_COORD = (
    CoordinacionStatus.PENDIENTE,
    CoordinacionStatus.PROGRAMADO,
    CoordinacionStatus.ABASTECIDO,
    CoordinacionStatus.AUSENTE,
)


def _day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    start_local = datetime.combine(day, time.min, tzinfo=settings.tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _booking_json(b: Booking) -> dict:
    local = b.starts_at.astimezone(settings.tz)
    ab = b.abastecedora
    op = b.operador
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
        "status": b.status,
        "coordinacion_status": b.coordinacion_status,
        "origen": b.origen,
        "sobreturno": b.sobreturno,
        "combustible_declarado": b.combustible_declarado,
        "primera_carga": b.primera_carga,
        "unknown_matricula": b.unknown_matricula,
        "badge_primera_carga": bool(b.primera_carga or b.unknown_matricula),
        "combustible_reconfirmado_en_persona": b.combustible_reconfirmado_en_persona,
        "reconfirm_pregunte_en_persona": b.reconfirm_pregunte_en_persona,
        "reconfirm_coincide_declarado": b.reconfirm_coincide_declarado,
        "cliente": user.display_name if user else "",
        "cliente_email": user.email if user else "",
        "empresa": (user.company if user else "") or "",
        "agenda_id": b.agenda_id,
        "agenda_name": b.agenda.full_name if b.agenda else "",
        "abastecedora_id": b.abastecedora_id,
        "abastecedora": ab.nombre if ab else None,
        "operador_user_id": b.operador_user_id,
        "operador": op.display_name if op else None,
        "cancelado_motivo": b.cancelado_motivo,
        "ausente_motivo": b.ausente_motivo,
    }


def _get_booking(db: Session, booking_id: int) -> Booking:
    booking = db.scalar(
        select(Booking)
        .options(
            selectinload(Booking.agenda),
            selectinload(Booking.user),
            selectinload(Booking.abastecedora),
            selectinload(Booking.operador),
        )
        .where(Booking.id == booking_id)
    )
    if booking is None:
        raise HTTPException(status_code=404, detail="No encontramos ese turno.")
    return booking


def _require_active(booking: Booking) -> None:
    if booking.status != BookingStatus.CONFIRMED:
        raise HTTPException(status_code=409, detail="Ese turno no está confirmado.")
    if booking.coordinacion_status == CoordinacionStatus.CANCELADO:
        raise HTTPException(status_code=409, detail="Ese turno ya fue cancelado.")


# ============================================================
# Matrícula API (nivel1+)
# ============================================================
@router.get("/api/matricula/{matricula}")
def api_matricula(
    matricula: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return lookup_matricula(db, matricula).as_dict()


# ============================================================
# Panel HTML
# ============================================================
@router.get("/coord")
def coord_panel(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    agendas = db.scalars(
        select(Agenda).where(Agenda.is_active.is_(True)).order_by(Agenda.sort_order, Agenda.name)
    ).all()
    operadores = db.scalars(
        select(User)
        .where(User.role.in_([Role.NIVEL_1, Role.NIVEL_2]), User.is_blocked.is_(False))
        .order_by(User.name, User.email)
    ).all()
    abastecedoras = db.scalars(
        select(Abastecedora).where(Abastecedora.activo.is_(True)).order_by(Abastecedora.sort_order, Abastecedora.nombre)
    ).all()
    today = datetime.now(settings.tz).date()
    return templates.TemplateResponse(
        request,
        "coord/board.html",
        {
            "user": admin,
            "agendas": agendas,
            "operadores": [
                {"id": u.id, "name": u.display_name, "email": u.email} for u in operadores
            ],
            "abastecedoras": [
                {
                    "id": a.id,
                    "nombre": a.nombre,
                    "grado": a.grado,
                    "agenda_id": a.agenda_id,
                    "fuera_de_servicio_hasta": a.fuera_de_servicio_hasta.isoformat()
                    if a.fuera_de_servicio_hasta
                    else None,
                }
                for a in abastecedoras
            ],
            "today": today.isoformat(),
        },
    )


# ============================================================
# Board + abastecedoras
# ============================================================
@router.get("/coord/board")
def coord_board(
    agenda_id: int | None = None,
    date_str: str | None = Query(None, alias="date"),
    status_filter: str | None = Query(None, alias="status"),
    q: str = "",
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
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

    # KPIs sobre el día/agenda sin filtro de estado/q (salvo agenda+date)
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

    return {"ok": True, "date": day.isoformat(), "agenda_id": agenda_id, "kpis": kpis, "bookings": items}


@router.get("/coord/abastecedoras")
def list_abastecedoras(
    agenda_id: int | None = None,
    grado: str | None = None,
    day: str | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
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
        if grado and not grados_compatibles(a.grado, grado):
            # still list but mark grado_ok false
            grado_ok = False
        else:
            grado_ok = True if not grado else True
        if grado:
            grado_ok = grados_compatibles(a.grado, grado)
        en_taller = bool(a.fuera_de_servicio_hasta and a.fuera_de_servicio_hasta >= ref_day)
        out.append(
            {
                "id": a.id,
                "nombre": a.nombre,
                "codigo": a.codigo,
                "grado": a.grado,
                "agenda_id": a.agenda_id,
                "grado_ok": grado_ok,
                "en_taller_hoy": en_taller and a.fuera_de_servicio_hasta == ref_day
                if a.fuera_de_servicio_hasta
                else False,
                "fuera_de_servicio_hasta": a.fuera_de_servicio_hasta.isoformat()
                if a.fuera_de_servicio_hasta
                else None,
                "disabled_today": bool(
                    a.fuera_de_servicio_hasta and a.fuera_de_servicio_hasta >= ref_day
                    and ref_day <= a.fuera_de_servicio_hasta
                ),
            }
        )
    return {"ok": True, "abastecedoras": out}


# ============================================================
# Acciones de estado
# ============================================================
class AsignarBody(BaseModel):
    abastecedora_id: int
    operador_user_id: int


class ReconfirmarBody(BaseModel):
    reconfirm_pregunte_en_persona: bool = False
    reconfirm_coincide_declarado: bool = False
    combustible_reconfirmado_en_persona: bool = False
    combustible: str | None = None


class MotivoBody(BaseModel):
    motivo: str = ""


class CancelarBody(BaseModel):
    motivo: str = Field(min_length=1, max_length=300)

    @field_validator("motivo")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("El motivo de cancelación es obligatorio.")
        return v


class ManualBody(BaseModel):
    agenda_id: int
    starts_at: datetime
    aircraft: str = Field(min_length=1, max_length=40)
    aircraft_model: str = Field(default="", max_length=60)
    liters: int | None = Field(default=None, ge=1, le=20_000)
    notes: str = Field(default="", max_length=500)
    combustible: str | None = None
    sobreturno: bool = False
    flight_number: str = Field(default="", max_length=20)

    @field_validator("aircraft")
    @classmethod
    def aircraft_ok(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("La matrícula es obligatoria.")
        return v

    @field_validator("starts_at")
    @classmethod
    def ensure_tz(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=settings.tz)
        return value.astimezone(UTC)


def _asignar(db: Session, booking: Booking, body: AsignarBody, admin: User) -> Booking:
    _require_active(booking)
    if booking.coordinacion_status not in (
        CoordinacionStatus.PENDIENTE,
        CoordinacionStatus.PROGRAMADO,
    ):
        raise HTTPException(
            status_code=409,
            detail="Solo se puede asignar un turno pendiente o programado.",
        )

    ab = db.get(Abastecedora, body.abastecedora_id)
    if ab is None or not ab.activo:
        raise HTTPException(status_code=404, detail="Abastecedora inexistente.")
    op = db.get(User, body.operador_user_id)
    if op is None or op.role not in (Role.NIVEL_1, Role.NIVEL_2) or op.is_blocked:
        raise HTTPException(status_code=404, detail="Operador inválido.")

    fuel = booking.combustible_declarado or (booking.agenda.product if booking.agenda else "")
    if not grados_compatibles(ab.grado, fuel):
        raise HTTPException(
            status_code=409,
            detail=f"Grado distinto: la abastecedora es {ab.grado} y el turno declara {fuel}.",
        )

    day = booking.starts_at.astimezone(settings.tz).date()
    if ab.fuera_de_servicio_hasta and day <= ab.fuera_de_servicio_hasta:
        raise HTTPException(
            status_code=409,
            detail=f"{ab.nombre} está fuera de servicio hasta {ab.fuera_de_servicio_hasta.isoformat()}.",
        )

    booking.abastecedora_id = ab.id
    booking.operador_user_id = op.id
    booking.coordinacion_status = CoordinacionStatus.PROGRAMADO
    booking.asignado_at = datetime.now(UTC)
    booking.asignado_by = admin.id
    db.commit()
    return _get_booking(db, booking.id), ab, op


@router.post("/coord/bookings/{booking_id}/asignar")
def asignar(
    booking_id: int,
    body: AsignarBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    booking = _get_booking(db, booking_id)
    booking, ab, op = _asignar(db, booking, body, admin)
    return {
        "ok": True,
        "booking": _booking_json(booking),
        "message": f"Turno programado · {ab.nombre} · {op.display_name}",
    }


@router.post("/coord/bookings/{booking_id}/reasignar")
def reasignar(
    booking_id: int,
    body: AsignarBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    booking = _get_booking(db, booking_id)
    if booking.coordinacion_status != CoordinacionStatus.PROGRAMADO:
        raise HTTPException(status_code=409, detail="Solo se puede reasignar un turno programado.")
    booking, ab, op = _asignar(db, booking, body, admin)
    return {
        "ok": True,
        "booking": _booking_json(booking),
        "message": f"Turno reasignado · {ab.nombre} · {op.display_name}",
    }


@router.post("/coord/bookings/{booking_id}/reconfirmar")
def reconfirmar(
    booking_id: int,
    body: ReconfirmarBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    booking = _get_booking(db, booking_id)
    _require_active(booking)
    booking.reconfirm_pregunte_en_persona = body.reconfirm_pregunte_en_persona
    booking.reconfirm_coincide_declarado = body.reconfirm_coincide_declarado
    booking.combustible_reconfirmado_en_persona = body.combustible_reconfirmado_en_persona
    if body.combustible and body.combustible.strip():
        booking.combustible_declarado = body.combustible.strip()
    if body.combustible_reconfirmado_en_persona:
        booking.reconfirmado_at = datetime.now(UTC)
        booking.reconfirmado_by = admin.id
    db.commit()
    booking = _get_booking(db, booking_id)
    return {"ok": True, "booking": _booking_json(booking)}


@router.post("/coord/bookings/{booking_id}/abastecer")
def abastecer(
    booking_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    booking = _get_booking(db, booking_id)
    _require_active(booking)
    if booking.coordinacion_status != CoordinacionStatus.PROGRAMADO:
        raise HTTPException(status_code=409, detail="Solo un turno programado puede marcarse abastecido.")

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

    # Upsert matrícula → combustible SOLO acá
    fuel = booking.combustible_declarado or (booking.agenda.product if booking.agenda else "")
    if booking.aircraft and fuel:
        upsert_matricula_combustible(db, raw_matricula=booking.aircraft, combustible=fuel)

    db.commit()
    booking = _get_booking(db, booking_id)
    return {"ok": True, "booking": _booking_json(booking), "message": "Turno marcado como abastecido."}


@router.post("/coord/bookings/{booking_id}/ausente")
def ausente(
    booking_id: int,
    body: MotivoBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    booking = _get_booking(db, booking_id)
    _require_active(booking)
    if booking.coordinacion_status != CoordinacionStatus.PROGRAMADO:
        raise HTTPException(status_code=409, detail="Solo un turno programado puede marcarse ausente.")
    booking.coordinacion_status = CoordinacionStatus.AUSENTE
    booking.ausente_at = datetime.now(UTC)
    booking.ausente_motivo = (body.motivo or "").strip()[:300]
    db.commit()
    booking = _get_booking(db, booking_id)
    return {"ok": True, "booking": _booking_json(booking), "message": "Marcado como no se presentó."}


@router.post("/coord/bookings/{booking_id}/cancelar")
def cancelar_coord(
    booking_id: int,
    body: CancelarBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    booking = _get_booking(db, booking_id)
    if booking.status == BookingStatus.CANCELLED:
        return {"ok": True, "message": "Ese turno ya estaba cancelado."}
    if booking.coordinacion_status not in (
        CoordinacionStatus.PENDIENTE,
        CoordinacionStatus.PROGRAMADO,
    ):
        raise HTTPException(
            status_code=409,
            detail="Solo se puede cancelar un turno pendiente o programado.",
        )

    now = datetime.now(UTC)
    booking.coordinacion_status = CoordinacionStatus.CANCELADO
    booking.cancelado_coord_at = now
    booking.cancelado_motivo = body.motivo
    # Sync: cancelado coord = Booking.cancelled
    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = now
    booking.cancelled_by_admin = True
    db.commit()
    return {"ok": True, "message": "Reserva cancelada."}


@router.post("/coord/bookings/manual", status_code=status.HTTP_201_CREATED)
def crear_manual(
    body: ManualBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    agenda = db.get(Agenda, body.agenda_id)
    if agenda is None or not agenda.is_active:
        raise HTTPException(status_code=404, detail="Agenda inexistente.")

    starts = body.starts_at
    ends = starts + timedelta(minutes=agenda.slot_minutes)

    lookup = lookup_matricula(db, body.aircraft)
    fuel = (body.combustible or "").strip() or lookup.combustible or agenda.product or ""

    if not body.sobreturno:
        with lock_agenda(db, agenda):
            slot = find_slot(db, agenda, starts, user_id=admin.id)
            # Staff puede elegir hora fuera de grilla: si no hay slot, igual permitir solo con sobreturno.
            # Sin sobreturno exigimos slot bookable o al menos existencia con cupo.
            if slot is None:
                # Contar ocupación puntual
                taken = db.scalar(
                    select(func.count(Booking.id)).where(
                        Booking.agenda_id == agenda.id,
                        Booking.status == BookingStatus.CONFIRMED,
                        Booking.starts_at == starts,
                        Booking.sobreturno.is_(False),
                    )
                ) or 0
                if taken >= agenda.capacity:
                    raise HTTPException(
                        status_code=409,
                        detail="Sin cupo en ese horario — usá sobreturno o elegí otra hora.",
                    )
            else:
                # Recalcular taken excluyendo sobreturnos ya está en slots.py
                if slot.status == SlotStatus.TAKEN:
                    raise HTTPException(
                        status_code=409,
                        detail="Sin cupo en ese horario — usá sobreturno o elegí otra hora.",
                    )
                ends = slot.ends_at
                starts = slot.starts_at

            booking = Booking(
                agenda_id=agenda.id,
                user_id=admin.id,
                starts_at=starts,
                ends_at=ends,
                status=BookingStatus.CONFIRMED,
                aircraft=body.aircraft.strip().upper()[:40],
                aircraft_model=(body.aircraft_model or "").strip()[:60],
                liters=body.liters,
                flight_number=(body.flight_number or "").strip().upper()[:20],
                notes=(body.notes or "").strip()[:500],
                origen=OrigenBooking.MANUAL,
                sobreturno=False,
                coordinacion_status=CoordinacionStatus.PENDIENTE,
                combustible_declarado=fuel,
                primera_carga=lookup.primera_carga,
                unknown_matricula=lookup.unknown_matricula,
            )
            db.add(booking)
            db.commit()
            db.refresh(booking)
    else:
        booking = Booking(
            agenda_id=agenda.id,
            user_id=admin.id,
            starts_at=starts,
            ends_at=ends,
            status=BookingStatus.CONFIRMED,
            aircraft=body.aircraft.strip().upper()[:40],
            aircraft_model=(body.aircraft_model or "").strip()[:60],
            liters=body.liters,
            flight_number=(body.flight_number or "").strip().upper()[:20],
            notes=(body.notes or "").strip()[:500],
            origen=OrigenBooking.MANUAL,
            sobreturno=True,
            coordinacion_status=CoordinacionStatus.PENDIENTE,
            combustible_declarado=fuel,
            primera_carga=lookup.primera_carga,
            unknown_matricula=lookup.unknown_matricula,
        )
        db.add(booking)
        db.commit()
        db.refresh(booking)

    booking = _get_booking(db, booking.id)
    return {"ok": True, "booking": _booking_json(booking), "message": "Turno manual creado."}


# ============================================================
# CRUD abastecedoras (nivel2 preferible; nivel1 puede ver; create/edit nivel2)
# ============================================================
class AbastecedoraBody(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    codigo: str | None = None
    grado: str = Field(min_length=1, max_length=40)
    agenda_id: int | None = None
    activo: bool = True
    fuera_de_servicio_hasta: date | None = None
    sort_order: int = 0


@router.post("/admin/abastecedoras", status_code=status.HTTP_201_CREATED)
def create_abastecedora(
    body: AbastecedoraBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if not admin.can_manage_users and admin.role != Role.NIVEL_1:
        raise HTTPException(status_code=403, detail="Sin permisos.")
    row = Abastecedora(
        nombre=body.nombre.strip(),
        codigo=(body.codigo or "").strip() or None,
        grado=normalize_grado(body.grado) or body.grado.strip(),
        agenda_id=body.agenda_id,
        activo=body.activo,
        fuera_de_servicio_hasta=body.fuera_de_servicio_hasta,
        sort_order=body.sort_order,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "id": row.id}


@router.patch("/admin/abastecedoras/{ab_id}")
def patch_abastecedora(
    ab_id: int,
    body: AbastecedoraBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    row = db.get(Abastecedora, ab_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Abastecedora inexistente.")
    row.nombre = body.nombre.strip()
    row.codigo = (body.codigo or "").strip() or None
    row.grado = normalize_grado(body.grado) or body.grado.strip()
    row.agenda_id = body.agenda_id
    row.activo = body.activo
    row.fuera_de_servicio_hasta = body.fuera_de_servicio_hasta
    row.sort_order = body.sort_order
    db.commit()
    return {"ok": True}
