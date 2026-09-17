"""Maestros editable: Aeronaves, Hangares, Abastecedoras, Operadores, Usuarios.

Acceso: nivel1 + nivel2 (require_admin). Cliente/operador: sin acceso.
Usuarios: nivel1 ve; cambios de rol / alta / bloqueo / eliminar → solo nivel2.
"""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth import require_admin
from app.config import settings
from app.database import get_db
from app.emails import booking_payload, send_cancellation
from app.matricula import normalize_grado, normalize_matricula
from app.models import (
    ROLE_LABELS,
    Abastecedora,
    Agenda,
    Booking,
    BookingStatus,
    CoordinacionStatus,
    Hangar,
    MatriculaCombustible,
    Operador,
    Role,
    User,
)
from app.templating import templates

router = APIRouter(tags=["maestros"])

ACTIVE_TURNOS = (CoordinacionStatus.PENDIENTE, CoordinacionStatus.PROGRAMADO)


def _norm_nombre(name: str) -> str:
    s = unicodedata.normalize("NFKC", (name or "").strip())
    s = " ".join(s.split())
    return s.casefold()


def _grado_badge(grado: str | None) -> str:
    g = normalize_grado(grado or "") or (grado or "")
    return g


def _aeronave_json(row: MatriculaCombustible) -> dict:
    h = row.hangar
    modelo = row.modelo or ""
    tipo = row.tipo or ""
    return {
        "id": row.id,
        "matricula": row.matricula,
        "matricula_display": row.matricula_display or row.matricula,
        "combustible": row.combustible,
        "grado": _grado_badge(row.combustible),
        "modelo": modelo,
        # Maestros columna TIPO: Avion del master (tipo o modelo)
        "tipo": tipo or modelo,
        "motor": row.motor or "",
        "cliente": row.cliente or "",
        "hangar_id": row.hangar_id,
        "hangar": h.nombre if h else "",
        "hangar_codigo": h.codigo if h else "",
        "capacidad_l": row.capacidad_l,
        "activo": row.activo,
    }


def _hangar_json(h: Hangar) -> dict:
    return {
        "id": h.id,
        "codigo": h.codigo,
        "nombre": h.nombre,
        "agenda_id": h.agenda_id,
        "ambito": "Global" if h.agenda_id is None else f"Planta #{h.agenda_id}",
        "capacidad": h.capacidad,
        "activo": h.activo,
    }


def _ab_json(a: Abastecedora) -> dict:
    return {
        "id": a.id,
        "codigo": a.codigo or "",
        "nombre": a.nombre,
        "grado": a.grado,
        "capacidad_l": a.capacidad_l,
        "agenda_id": a.agenda_id,
        "ambito": "Global" if a.agenda_id is None else f"Planta #{a.agenda_id}",
        "activo": a.activo,
        "fuera_de_servicio_hasta": a.fuera_de_servicio_hasta.isoformat()
        if a.fuera_de_servicio_hasta
        else None,
        "sort_order": a.sort_order,
        "estado": "Activa" if a.activo else "Fuera de servicio",
    }


def _op_json(o: Operador) -> dict:
    return {
        "id": o.id,
        "nombre": o.nombre,
        "activo": o.activo,
        "agenda_id": o.agenda_id,
        "ambito": "Global" if o.agenda_id is None else f"Planta #{o.agenda_id}",
        "user_id": o.user_id,
        "user_email": o.user.email if o.user else None,
        "user_name": o.user.display_name if o.user else None,
    }


def _user_json(u: User) -> dict:
    last_login = None
    if u.last_login_at:
        last_login = u.last_login_at.astimezone(settings.tz).strftime("%d/%m/%Y %H:%M")
    return {
        "id": u.id,
        "name": u.name or "",
        "email": u.email,
        "role": u.role,
        "role_label": ROLE_LABELS.get(u.role, u.role),
        "company": u.company or "",
        "phone": u.phone or "",
        "is_blocked": u.is_blocked,
        "google_sub": bool(u.google_sub),
        "last_login_at": last_login,
    }


def _cancel_active_turnos_matricula(db: Session, matricula_key: str) -> list[Booking]:
    """Cancela turnos activos (PENDIENTE/PROGRAMADO) de esa matrícula al cambiar grado.

    Devuelve la lista de bookings cancelados (con agenda/user cargados) para
    poder armar los emails de aviso sin otra query.
    """
    like_keys = {matricula_key, matricula_key.upper()}
    rows = db.scalars(
        select(Booking)
        .options(selectinload(Booking.agenda), selectinload(Booking.user))
        .where(
            Booking.status == BookingStatus.CONFIRMED,
            Booking.coordinacion_status.in_(ACTIVE_TURNOS),
        )
    ).all()
    now = datetime.now(UTC)
    cancelled: list[Booking] = []
    for b in rows:
        key = normalize_matricula(b.aircraft or "")
        if key not in like_keys and key != matricula_key:
            continue
        b.coordinacion_status = CoordinacionStatus.CANCELADO
        b.cancelado_coord_at = now
        b.cancelado_motivo = "Cancelado por cambio de grado de matrícula"
        b.status = BookingStatus.CANCELLED
        b.cancelled_at = now
        b.cancelled_by_admin = True
        cancelled.append(b)
    return cancelled


def _count_level2(db: Session, *, excluding: int | None = None) -> int:
    q = select(func.count(User.id)).where(User.role == Role.NIVEL_2, User.is_blocked.is_(False))
    if excluding is not None:
        q = q.where(User.id != excluding)
    return db.scalar(q) or 0


def _maestro_agenda_scope(column, agenda_id: int | None):
    """Filtro de planta para maestros con agenda_id nullable.

    - agenda_id None (UI «Todas / global»): sin filtro → globales (NULL) + todas las plantas.
    - agenda_id set: globales (agenda_id IS NULL) OR de esa planta.
    Nunca ocultar filas globales al filtrar por planta.
    """
    if agenda_id is None:
        return None
    return or_(column.is_(None), column == agenda_id)


# ============================================================
# Página HTML
# ============================================================
@router.get("/coord/maestros")
def maestros_page(
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    agendas = db.scalars(
        select(Agenda).where(Agenda.is_active.is_(True)).order_by(Agenda.sort_order, Agenda.name)
    ).all()
    hangares = db.scalars(select(Hangar).order_by(Hangar.codigo)).all()
    return templates.TemplateResponse(
        request,
        "coord/maestros.html",
        {
            "user": admin,
            "agendas": agendas,
            "hangares": [_hangar_json(h) for h in hangares],
            "can_manage_users": admin.can_manage_users,
            "role_labels": {str(getattr(k, "value", k)): v for k, v in ROLE_LABELS.items()},
            "roles": [Role.CLIENTE.value, Role.OPERADOR.value, Role.NIVEL_1.value, Role.NIVEL_2.value],
        },
    )


# ============================================================
# Re-sembrar maestros (hangares + abastecedoras + operadores)
# ============================================================
@router.post("/coord/maestros/reseed")
def reseed_maestros(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Nivel 1+: corre el seed idempotente desde data/*.csv y devuelve counts."""
    _ = admin
    from scripts.seed_flota_operadores import run_seed

    # Usa la misma sesión del request; run_seed hace commit.
    result = run_seed(db=db, skip_migrate=True, dry_run=False)
    counts = result["counts"]
    hg = result["hangares"] or {"inserted": 0, "updated": 0, "unchanged": 0, "total_csv": 0}
    ab = result["abastecedoras"]
    op = result["operadores"]
    message = (
        f"Re-sembrado: Hangares {counts['hangares']} "
        f"(+{hg['inserted']}~{hg['updated']}), "
        f"Abastecedoras {counts['abastecedoras']} "
        f"(+{ab['inserted']}~{ab['updated']}), "
        f"Operadores {counts['operadores']} "
        f"(+{op['inserted']}~{op['updated']})."
    )
    return {
        "ok": True,
        "message": message,
        "counts": counts,
        "stats": {
            "hangares": result["hangares"],
            "abastecedoras": result["abastecedoras"],
            "operadores": result["operadores"],
        },
    }


# ============================================================
# Aeronaves
# ============================================================
class AeronaveBody(BaseModel):
    matricula: str = Field(min_length=1, max_length=40)
    combustible: str | None = None
    modelo: str = ""
    tipo: str = ""
    motor: str = ""
    cliente: str = ""
    hangar_id: int | None = None
    capacidad_l: int | None = Field(default=None, ge=0, le=1_000_000)
    activo: bool = True

    @field_validator("matricula")
    @classmethod
    def mat_ok(cls, v: str) -> str:
        v = v.strip().upper()
        if not normalize_matricula(v):
            raise ValueError("Matrícula inválida.")
        return v


class CambiarGradoBody(BaseModel):
    grado: str = Field(min_length=1, max_length=40)
    confirmacion: str = Field(min_length=1, max_length=80)


@router.get("/coord/maestros/aeronaves")
def list_aeronaves(
    q: str = "",
    grado: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    stmt = (
        select(MatriculaCombustible)
        .options(selectinload(MatriculaCombustible.hangar))
        .order_by(MatriculaCombustible.matricula)
    )
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                MatriculaCombustible.matricula.ilike(like),
                MatriculaCombustible.matricula_display.ilike(like),
                MatriculaCombustible.cliente.ilike(like),
                MatriculaCombustible.modelo.ilike(like),
                MatriculaCombustible.tipo.ilike(like),
            )
        )
    if grado:
        ng = normalize_grado(grado)
        stmt = stmt.where(MatriculaCombustible.combustible.ilike(f"%{ng.split()[0]}%"))
    count_stmt = select(func.count(MatriculaCombustible.id))
    if q.strip():
        like = f"%{q.strip()}%"
        count_stmt = count_stmt.where(
            or_(
                MatriculaCombustible.matricula.ilike(like),
                MatriculaCombustible.matricula_display.ilike(like),
                MatriculaCombustible.cliente.ilike(like),
                MatriculaCombustible.modelo.ilike(like),
                MatriculaCombustible.tipo.ilike(like),
            )
        )
    if grado:
        ng = normalize_grado(grado)
        count_stmt = count_stmt.where(MatriculaCombustible.combustible.ilike(f"%{ng.split()[0]}%"))
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.offset(offset).limit(limit)).all()
    return {"ok": True, "total": total, "items": [_aeronave_json(r) for r in rows]}


@router.post("/coord/maestros/aeronaves", status_code=status.HTTP_201_CREATED)
def create_aeronave(
    body: AeronaveBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    key = normalize_matricula(body.matricula)
    if db.scalar(select(MatriculaCombustible).where(MatriculaCombustible.matricula == key)):
        raise HTTPException(status_code=409, detail="Ya existe esa matrícula.")
    fuel = normalize_grado(body.combustible or "") or (body.combustible or "").strip() or None
    row = MatriculaCombustible(
        matricula=key,
        matricula_display=body.matricula.strip().upper()[:40],
        combustible=fuel,
        modelo=(body.modelo or "").strip()[:80],
        tipo=(body.tipo or "").strip()[:60],
        motor=(body.motor or "").strip()[:60],
        cliente=(body.cliente or "").strip()[:200],
        hangar_id=body.hangar_id,
        capacidad_l=body.capacidad_l,
        activo=body.activo,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    row = db.scalar(
        select(MatriculaCombustible)
        .options(selectinload(MatriculaCombustible.hangar))
        .where(MatriculaCombustible.id == row.id)
    )
    return {"ok": True, "item": _aeronave_json(row)}


@router.patch("/coord/maestros/aeronaves/{row_id}")
def patch_aeronave(
    row_id: int,
    body: AeronaveBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    row = db.get(MatriculaCombustible, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Aeronave inexistente.")
    # Editar no cambia grado (usar Cambiar grado)
    row.matricula_display = body.matricula.strip().upper()[:40]
    row.modelo = (body.modelo or "").strip()[:80]
    row.tipo = (body.tipo or "").strip()[:60]
    row.motor = (body.motor or "").strip()[:60]
    row.cliente = (body.cliente or "").strip()[:200]
    row.hangar_id = body.hangar_id
    row.capacidad_l = body.capacidad_l
    row.activo = body.activo
    db.commit()
    row = db.scalar(
        select(MatriculaCombustible)
        .options(selectinload(MatriculaCombustible.hangar))
        .where(MatriculaCombustible.id == row_id)
    )
    return {"ok": True, "item": _aeronave_json(row)}


@router.post("/coord/maestros/aeronaves/{row_id}/cambiar-grado")
def cambiar_grado_aeronave(
    row_id: int,
    body: CambiarGradoBody,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    row = db.get(MatriculaCombustible, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Aeronave inexistente.")
    display = (row.matricula_display or row.matricula).upper()
    expected = f"CAMBIAR GRADO {display}"
    if body.confirmacion.strip().upper() != expected:
        raise HTTPException(
            status_code=400,
            detail=f"Confirmación incorrecta. Escribí exactamente: {expected}",
        )
    new_g = normalize_grado(body.grado) or body.grado.strip()
    if not new_g:
        raise HTTPException(status_code=400, detail="Grado inválido.")
    row.combustible = new_g
    cancelled_bookings = _cancel_active_turnos_matricula(db, row.matricula)
    # Armar payloads antes del commit/cierre de sesión (ORM detached después).
    mail_payloads = []
    for b in cancelled_bookings:
        if b.agenda is None or b.user is None:
            continue
        mail_payloads.append(booking_payload(b, b.agenda, b.user))
    db.commit()
    # Emails en background: falla graceful (log) y NUNCA bloquea el cambio de grado.
    for data in mail_payloads:
        background.add_task(
            send_cancellation, data, by_admin=True, reason="cambio_grado"
        )
    cancelled = len(cancelled_bookings)
    row = db.scalar(
        select(MatriculaCombustible)
        .options(selectinload(MatriculaCombustible.hangar))
        .where(MatriculaCombustible.id == row_id)
    )
    return {
        "ok": True,
        "item": _aeronave_json(row),
        "cancelled": cancelled,
        "message": f"Grado actualizado a {new_g}."
        + (f" Se cancelaron {cancelled} turno(s) activo(s)." if cancelled else ""),
    }


@router.delete("/coord/maestros/aeronaves/{row_id}")
def delete_aeronave(
    row_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    row = db.get(MatriculaCombustible, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Aeronave inexistente.")
    key = row.matricula
    in_use = False
    for b in db.scalars(
        select(Booking).where(
            Booking.status == BookingStatus.CONFIRMED,
            Booking.coordinacion_status.in_(ACTIVE_TURNOS),
        )
    ).all():
        if normalize_matricula(b.aircraft or "") == key:
            in_use = True
            break
    if in_use:
        raise HTTPException(
            status_code=409,
            detail="Hay turnos activos con esa matrícula. Cancelalos o desactivá la aeronave.",
        )
    db.delete(row)
    db.commit()
    return {"ok": True}


# ============================================================
# Hangares
# ============================================================
class HangarBody(BaseModel):
    codigo: str = Field(min_length=1, max_length=40)
    nombre: str = Field(min_length=1, max_length=160)
    agenda_id: int | None = None
    capacidad: int | None = Field(default=None, ge=0, le=10_000)
    activo: bool = True

    @field_validator("codigo")
    @classmethod
    def codigo_ok(cls, v: str) -> str:
        return v.strip().upper()[:40]


@router.get("/coord/maestros/hangares")
def list_hangares(
    agenda_id: int | None = Query(None, description="Planta; omitir = Todas/global (incluye agenda_id NULL)"),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    stmt = select(Hangar).order_by(Hangar.codigo)
    scope = _maestro_agenda_scope(Hangar.agenda_id, agenda_id)
    if scope is not None:
        stmt = stmt.where(scope)
    rows = db.scalars(stmt).all()
    return {"ok": True, "total": len(rows), "items": [_hangar_json(h) for h in rows]}


@router.post("/coord/maestros/hangares", status_code=status.HTTP_201_CREATED)
def create_hangar(
    body: HangarBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    if db.scalar(select(Hangar).where(Hangar.codigo == body.codigo)):
        raise HTTPException(status_code=409, detail="Ya existe un hangar con ese código.")
    row = Hangar(
        codigo=body.codigo,
        nombre=body.nombre.strip()[:160],
        agenda_id=body.agenda_id,
        capacidad=body.capacidad,
        activo=body.activo,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": _hangar_json(row)}


@router.patch("/coord/maestros/hangares/{hid}")
def patch_hangar(
    hid: int,
    body: HangarBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    row = db.get(Hangar, hid)
    if row is None:
        raise HTTPException(status_code=404, detail="Hangar inexistente.")
    other = db.scalar(select(Hangar).where(Hangar.codigo == body.codigo, Hangar.id != hid))
    if other:
        raise HTTPException(status_code=409, detail="Código ya usado.")
    row.codigo = body.codigo
    row.nombre = body.nombre.strip()[:160]
    row.agenda_id = body.agenda_id
    row.capacidad = body.capacidad
    row.activo = body.activo
    db.commit()
    return {"ok": True, "item": _hangar_json(row)}


@router.post("/coord/maestros/hangares/{hid}/desactivar")
def desactivar_hangar(
    hid: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    row = db.get(Hangar, hid)
    if row is None:
        raise HTTPException(status_code=404, detail="Hangar inexistente.")
    row.activo = not row.activo
    db.commit()
    return {"ok": True, "item": _hangar_json(row)}


@router.delete("/coord/maestros/hangares/{hid}")
def delete_hangar(
    hid: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    row = db.get(Hangar, hid)
    if row is None:
        raise HTTPException(status_code=404, detail="Hangar inexistente.")
    used = db.scalar(
        select(func.count(MatriculaCombustible.id)).where(MatriculaCombustible.hangar_id == hid)
    ) or 0
    if used:
        raise HTTPException(
            status_code=409,
            detail="Hay aeronaves vinculadas. Desactivá el hangar en lugar de eliminarlo.",
        )
    db.delete(row)
    db.commit()
    return {"ok": True}


# ============================================================
# Abastecedoras
# ============================================================
class AbastecedoraMaestroBody(BaseModel):
    codigo: str | None = None
    nombre: str = Field(min_length=1, max_length=80)
    grado: str = Field(min_length=1, max_length=40)
    capacidad_l: int | None = Field(default=None, ge=0)
    agenda_id: int | None = None
    activo: bool = True
    sort_order: int = 0


@router.get("/coord/maestros/abastecedoras")
def list_abs(
    agenda_id: int | None = Query(None, description="Planta; omitir = Todas/global (incluye agenda_id NULL)"),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    stmt = select(Abastecedora).order_by(Abastecedora.sort_order, Abastecedora.codigo, Abastecedora.nombre)
    scope = _maestro_agenda_scope(Abastecedora.agenda_id, agenda_id)
    if scope is not None:
        stmt = stmt.where(scope)
    rows = db.scalars(stmt).all()
    return {"ok": True, "total": len(rows), "items": [_ab_json(a) for a in rows]}


@router.post("/coord/maestros/abastecedoras", status_code=status.HTTP_201_CREATED)
def create_abs(
    body: AbastecedoraMaestroBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    codigo = (body.codigo or "").strip().upper() or None
    if codigo and db.scalar(select(Abastecedora).where(Abastecedora.codigo == codigo)):
        raise HTTPException(status_code=409, detail="Código ya usado.")
    row = Abastecedora(
        codigo=codigo,
        nombre=body.nombre.strip()[:80],
        grado=normalize_grado(body.grado) or body.grado.strip(),
        capacidad_l=body.capacidad_l,
        agenda_id=body.agenda_id,
        activo=body.activo,
        sort_order=body.sort_order,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": _ab_json(row)}


@router.patch("/coord/maestros/abastecedoras/{aid}")
def patch_abs(
    aid: int,
    body: AbastecedoraMaestroBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    row = db.get(Abastecedora, aid)
    if row is None:
        raise HTTPException(status_code=404, detail="Abastecedora inexistente.")
    codigo = (body.codigo or "").strip().upper() or None
    if codigo:
        other = db.scalar(select(Abastecedora).where(Abastecedora.codigo == codigo, Abastecedora.id != aid))
        if other:
            raise HTTPException(status_code=409, detail="Código ya usado.")
    row.codigo = codigo
    row.nombre = body.nombre.strip()[:80]
    # grado via cambiar-grado
    row.capacidad_l = body.capacidad_l
    row.agenda_id = body.agenda_id
    row.activo = body.activo
    row.sort_order = body.sort_order
    db.commit()
    return {"ok": True, "item": _ab_json(row)}


@router.post("/coord/maestros/abastecedoras/{aid}/cambiar-grado")
def cambiar_grado_abs(
    aid: int,
    body: CambiarGradoBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    row = db.get(Abastecedora, aid)
    if row is None:
        raise HTTPException(status_code=404, detail="Abastecedora inexistente.")
    label = (row.codigo or row.nombre).upper()
    expected = f"CAMBIAR GRADO {label}"
    if body.confirmacion.strip().upper() != expected:
        raise HTTPException(
            status_code=400,
            detail=f"Confirmación incorrecta. Escribí exactamente: {expected}",
        )
    new_g = normalize_grado(body.grado) or body.grado.strip()
    row.grado = new_g
    db.commit()
    return {"ok": True, "item": _ab_json(row), "message": f"Grado actualizado a {new_g}."}


@router.delete("/coord/maestros/abastecedoras/{aid}")
def delete_abs(
    aid: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    row = db.get(Abastecedora, aid)
    if row is None:
        raise HTTPException(status_code=404, detail="Abastecedora inexistente.")
    used = db.scalar(
        select(func.count(Booking.id)).where(
            Booking.abastecedora_id == aid,
            Booking.status == BookingStatus.CONFIRMED,
            Booking.coordinacion_status.in_(ACTIVE_TURNOS),
        )
    ) or 0
    if used:
        raise HTTPException(
            status_code=409,
            detail="Hay turnos activos asignados. Desactivá la abastecedora.",
        )
    db.delete(row)
    db.commit()
    return {"ok": True}


# ============================================================
# Operadores
# ============================================================
class OperadorBody(BaseModel):
    nombre: str = Field(min_length=1, max_length=160)
    activo: bool = True
    agenda_id: int | None = None
    user_id: int | None = None


@router.get("/coord/maestros/operadores")
def list_ops(
    agenda_id: int | None = Query(None, description="Planta; omitir = Todas/global (incluye agenda_id NULL)"),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    stmt = select(Operador).options(selectinload(Operador.user)).order_by(Operador.nombre)
    scope = _maestro_agenda_scope(Operador.agenda_id, agenda_id)
    if scope is not None:
        stmt = stmt.where(scope)
    rows = db.scalars(stmt).all()
    return {"ok": True, "total": len(rows), "items": [_op_json(o) for o in rows]}


@router.post("/coord/maestros/operadores", status_code=status.HTTP_201_CREATED)
def create_op(
    body: OperadorBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    nombre = body.nombre.strip()[:160]
    norm = _norm_nombre(nombre)
    if not norm:
        raise HTTPException(status_code=400, detail="Nombre inválido.")
    if db.scalar(select(Operador).where(Operador.nombre_norm == norm)):
        raise HTTPException(status_code=409, detail="Ya existe un operador con ese nombre.")
    if body.user_id is not None:
        u = db.get(User, body.user_id)
        if u is None:
            raise HTTPException(status_code=404, detail="Usuario inexistente.")
        if db.scalar(select(Operador).where(Operador.user_id == body.user_id)):
            raise HTTPException(status_code=409, detail="Ese usuario ya está vinculado a otro operador.")
    row = Operador(
        nombre=nombre,
        nombre_norm=norm,
        activo=body.activo,
        agenda_id=body.agenda_id,
        user_id=body.user_id,
    )
    db.add(row)
    db.commit()
    row = db.scalar(
        select(Operador).options(selectinload(Operador.user)).where(Operador.id == row.id)
    )
    return {"ok": True, "item": _op_json(row)}


@router.patch("/coord/maestros/operadores/{oid}")
def patch_op(
    oid: int,
    body: OperadorBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    row = db.get(Operador, oid)
    if row is None:
        raise HTTPException(status_code=404, detail="Operador inexistente.")
    nombre = body.nombre.strip()[:160]
    norm = _norm_nombre(nombre)
    other = db.scalar(select(Operador).where(Operador.nombre_norm == norm, Operador.id != oid))
    if other:
        raise HTTPException(status_code=409, detail="Nombre ya usado.")
    if body.user_id is not None:
        linked = db.scalar(select(Operador).where(Operador.user_id == body.user_id, Operador.id != oid))
        if linked:
            raise HTTPException(status_code=409, detail="Ese usuario ya está vinculado.")
    row.nombre = nombre
    row.nombre_norm = norm
    row.activo = body.activo
    row.agenda_id = body.agenda_id
    row.user_id = body.user_id
    db.commit()
    row = db.scalar(
        select(Operador).options(selectinload(Operador.user)).where(Operador.id == oid)
    )
    return {"ok": True, "item": _op_json(row)}


@router.post("/coord/maestros/operadores/{oid}/desactivar")
def desactivar_op(
    oid: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    row = db.get(Operador, oid)
    if row is None:
        raise HTTPException(status_code=404, detail="Operador inexistente.")
    row.activo = not row.activo
    db.commit()
    row = db.scalar(
        select(Operador).options(selectinload(Operador.user)).where(Operador.id == oid)
    )
    return {"ok": True, "item": _op_json(row)}


@router.delete("/coord/maestros/operadores/{oid}")
def delete_op(
    oid: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    _ = admin
    row = db.get(Operador, oid)
    if row is None:
        raise HTTPException(status_code=404, detail="Operador inexistente.")
    used = db.scalar(
        select(func.count(Booking.id)).where(
            Booking.operador_id == oid,
            Booking.status == BookingStatus.CONFIRMED,
            Booking.coordinacion_status.in_(ACTIVE_TURNOS),
        )
    ) or 0
    if used:
        raise HTTPException(status_code=409, detail="Hay turnos activos. Desactivá el operador.")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ============================================================
# Usuarios (tab 5)
# ============================================================
class UsuarioBody(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    name: str = ""
    role: str = Role.CLIENTE.value
    company: str = ""
    phone: str = ""
    is_blocked: bool = False

    @field_validator("email")
    @classmethod
    def email_ok(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v:
            raise ValueError("Email inválido.")
        return v


VALID_ROLES = {Role.CLIENTE.value, Role.OPERADOR.value, Role.NIVEL_1.value, Role.NIVEL_2.value}


@router.get("/coord/maestros/usuarios")
def list_usuarios(
    q: str = "",
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    stmt = select(User).order_by(User.role.desc(), User.email)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(User.email).like(like),
                func.lower(User.name).like(like),
                func.lower(User.company).like(like),
            )
        )
    rows = db.scalars(stmt.limit(500)).all()
    return {
        "ok": True,
        "total": len(rows),
        "can_manage": admin.can_manage_users,
        "items": [_user_json(u) for u in rows],
    }


@router.post("/coord/maestros/usuarios", status_code=status.HTTP_201_CREATED)
def create_usuario(
    body: UsuarioBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if not admin.can_manage_users:
        raise HTTPException(status_code=403, detail="Solo nivel 2 puede dar de alta usuarios.")
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="Rol inválido.")
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(status_code=409, detail="Ya existe un usuario con ese email.")
    row = User(
        email=body.email,
        name=(body.name or "").strip()[:160],
        role=body.role,
        company=(body.company or "").strip()[:160],
        phone=(body.phone or "").strip()[:40],
        is_blocked=body.is_blocked,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "item": _user_json(row)}


@router.patch("/coord/maestros/usuarios/{uid}")
def patch_usuario(
    uid: int,
    body: UsuarioBody,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    target = db.get(User, uid)
    if target is None:
        raise HTTPException(status_code=404, detail="Usuario inexistente.")
    # nivel1: solo lectura de rol; puede actualizar name/company si permitimos — handoff: view
    if not admin.can_manage_users:
        raise HTTPException(status_code=403, detail="Solo nivel 2 puede editar usuarios.")
    if body.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail="Rol inválido.")
    if target.id == admin.id and body.role != target.role:
        raise HTTPException(status_code=400, detail="No podés cambiarte el nivel a vos mismo.")
    if (
        target.role == Role.NIVEL_2
        and body.role != Role.NIVEL_2.value
        and _count_level2(db, excluding=target.id) == 0
    ):
        raise HTTPException(status_code=400, detail="Tiene que quedar al menos un nivel 2.")
    if body.is_blocked and target.id == admin.id:
        raise HTTPException(status_code=400, detail="No podés bloquearte a vos mismo.")
    if (
        body.is_blocked
        and not target.is_blocked
        and target.role == Role.NIVEL_2
        and _count_level2(db, excluding=target.id) == 0
    ):
        raise HTTPException(status_code=400, detail="Tiene que quedar al menos un nivel 2 activo.")

    target.name = (body.name or "").strip()[:160]
    target.company = (body.company or "").strip()[:160]
    target.phone = (body.phone or "").strip()[:40]
    target.role = body.role
    target.is_blocked = body.is_blocked
    # email no se cambia (identidad Google)
    db.commit()
    return {"ok": True, "item": _user_json(target)}


@router.post("/coord/maestros/usuarios/{uid}/desactivar")
def desactivar_usuario(
    uid: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if not admin.can_manage_users:
        raise HTTPException(status_code=403, detail="Solo nivel 2.")
    target = db.get(User, uid)
    if target is None:
        raise HTTPException(status_code=404, detail="Usuario inexistente.")
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="No podés bloquearte a vos mismo.")
    if (
        not target.is_blocked
        and target.role == Role.NIVEL_2
        and _count_level2(db, excluding=target.id) == 0
    ):
        raise HTTPException(status_code=400, detail="Tiene que quedar al menos un nivel 2 activo.")
    target.is_blocked = not target.is_blocked
    db.commit()
    return {"ok": True, "item": _user_json(target)}


@router.delete("/coord/maestros/usuarios/{uid}")
def delete_usuario(
    uid: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if not admin.can_manage_users:
        raise HTTPException(status_code=403, detail="Solo nivel 2.")
    target = db.get(User, uid)
    if target is None:
        raise HTTPException(status_code=404, detail="Usuario inexistente.")
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="No podés eliminarte a vos mismo.")
    if target.role == Role.NIVEL_2 and _count_level2(db, excluding=target.id) == 0:
        raise HTTPException(status_code=400, detail="Tiene que quedar al menos un nivel 2.")
    upcoming = db.scalar(
        select(func.count(Booking.id)).where(
            Booking.user_id == uid,
            Booking.status == BookingStatus.CONFIRMED,
            Booking.starts_at > datetime.now(UTC),
        )
    ) or 0
    if upcoming:
        raise HTTPException(
            status_code=409,
            detail="Tiene turnos futuros. Bloqueá la cuenta en lugar de eliminarla.",
        )
    db.delete(target)
    db.commit()
    return {"ok": True}
