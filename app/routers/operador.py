"""Panel operador de planta: turnos PROGRAMADOS asignados a él."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth import require_operador, require_operador_or_coord
from app.config import settings
from app.database import get_db
from app.fuel_banner import resolve_fuel_kind
from app.matricula import lookup_matricula, normalize_grado, normalize_matricula, upsert_matricula_combustible
from app.models import (
    Booking,
    BookingStatus,
    CoordinacionStatus,
    FuelScanLog,
    Operador,
    TomaFoto,
    User,
)
from app.templating import templates
from app.toma_qr import verify_toma_token
from app.toma_storage import (
    booking_has_toma_foto,
    save_toma_upload,
    toma_foto_counts_by_booking,
)

router = APIRouter(tags=["operador"])


def _day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    start_local = datetime.combine(day, time.min, tzinfo=settings.tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _booking_json(b: Booking, *, has_toma_foto: bool | None = None) -> dict:
    local = b.starts_at.astimezone(settings.tz)
    ab = b.abastecedora
    user = b.user
    data = {
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
    if has_toma_foto is not None:
        data["has_toma_foto"] = bool(has_toma_foto)
        data["toma_fotos_count"] = 1 if has_toma_foto else 0
    return data


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
    counts = toma_foto_counts_by_booking(db, [b.id for b in rows])
    return {
        "ok": True,
        "date": day.isoformat(),
        "bookings": [
            _booking_json(b, has_toma_foto=counts.get(b.id, 0) > 0) for b in rows
        ],
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

    if not booking_has_toma_foto(db, booking.id):
        raise HTTPException(
            status_code=409,
            detail="Hace falta al menos 1 foto de toma antes de marcar abastecido.",
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




# ============================================================
# Lookup matrícula → combustible (fail-closed)
# ============================================================


@router.get("/operador/api/matricula/{raw}")
def api_lookup_matricula_get(
    raw: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_operador_or_coord),
):
    """Dado matrícula → grado canónico del maestro. 404 si no hay combustible."""
    key = normalize_matricula(raw)
    if not key:
        raise HTTPException(status_code=400, detail="Indicá una matrícula válida.")
    result = lookup_matricula(db, key)
    fuel = (result.combustible or "").strip()
    if not result.found or not fuel:
        raise HTTPException(
            status_code=404,
            detail="Matrícula desconocida o sin combustible en el maestro.",
        )
    kind, label, _ = resolve_fuel_kind(fuel)
    return {
        "ok": True,
        "matricula": result.matricula,
        "matricula_key": key,
        "combustible": label or normalize_grado(fuel),
        "kind": kind,
        "modelo": result.modelo,
        "tipo": result.tipo,
    }


class MatriculaBody(BaseModel):
    matricula: str


@router.post("/operador/api/lookup-matricula")
def api_lookup_matricula_post(
    body: MatriculaBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_operador_or_coord),
):
    key = normalize_matricula(body.matricula)
    if not key:
        raise HTTPException(status_code=400, detail="Indicá una matrícula válida.")
    result = lookup_matricula(db, key)
    fuel = (result.combustible or "").strip()
    if not result.found or not fuel:
        raise HTTPException(
            status_code=404,
            detail="Matrícula desconocida o sin combustible en el maestro.",
        )
    kind, label, _ = resolve_fuel_kind(fuel)
    return {
        "ok": True,
        "matricula": result.matricula,
        "matricula_key": key,
        "combustible": label or normalize_grado(fuel),
        "kind": kind,
        "modelo": result.modelo,
        "tipo": result.tipo,
    }


# ============================================================
# Foto de toma (dataset)
# ============================================================


@router.post("/operador/api/toma-foto")
async def api_upload_toma(
    matricula: str = Form(...),
    booking_id: int | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_operador_or_coord),
):
    key = normalize_matricula(matricula)
    if not key:
        raise HTTPException(status_code=400, detail="Matrícula inválida.")
    if booking_id is not None:
        booking = db.get(Booking, booking_id)
        if booking is None:
            raise HTTPException(status_code=404, detail="No encontramos ese turno.")
    try:
        path_str, digest = await save_toma_upload(matricula=key, upload=file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    row = TomaFoto(
        matricula=key,
        booking_id=booking_id,
        user_id=user.id,
        path=path_str,
        sha256=digest,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": True,
        "id": row.id,
        "matricula": key,
        "booking_id": booking_id,
        "sha256": digest,
        "message": "Foto de toma guardada.",
    }


# ============================================================
# Scanner QR + confirmación + foto
# ============================================================


def _canonical_fuel_for_matricula(db: Session, matricula: str) -> tuple[str | None, str]:
    """→ (label JET A-1/AVGAS 100LL o None, kind). Fail-closed: None si desconocida."""
    result = lookup_matricula(db, matricula)
    fuel = (result.combustible or "").strip()
    if not result.found or not fuel:
        return None, "unknown"
    kind, label, _ = resolve_fuel_kind(fuel)
    return (label or normalize_grado(fuel)), kind or "unknown"


def _booking_context(db: Session, booking_id: int) -> Booking | None:
    return db.scalar(
        select(Booking)
        .options(selectinload(Booking.agenda), selectinload(Booking.user))
        .where(Booking.id == booking_id)
    )


@router.get("/operador/scan")
def operador_scan_page(
    request: Request,
    t: str | None = Query(None),
    user: User = Depends(require_operador_or_coord),
):
    """Página scanner: cámara + pegar token. Si ?t= viene, redirige a /operador/s/<t>."""
    if t and t.strip():
        return RedirectResponse(f"/operador/s/{t.strip()}", status_code=303)
    return templates.TemplateResponse(
        request,
        "operador/scan.html",
        {"user": user},
    )


@router.get("/operador/s/{token}")
def operador_scan_token(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_operador_or_coord),
):
    """Valida token firmado y muestra pantalla de confirmación (fail-closed)."""
    try:
        payload = verify_toma_token(token)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "operador/scan_result.html",
            {
                "user": user,
                "error": str(exc),
                "payload": None,
                "booking": None,
                "fuel_label": None,
                "fuel_kind": "unknown",
                "token": token,
            },
            status_code=400,
        )

    booking = _booking_context(db, payload.booking_id)
    fuel_label, fuel_kind = _canonical_fuel_for_matricula(db, payload.matricula)
    # Si el maestro no tiene combustible, no inventamos: error claro
    if fuel_label is None:
        return templates.TemplateResponse(
            request,
            "operador/scan_result.html",
            {
                "user": user,
                "error": (
                    f"Matrícula {payload.matricula} sin combustible en el maestro. "
                    "No se confirma producto."
                ),
                "payload": payload,
                "booking": booking,
                "fuel_label": None,
                "fuel_kind": "unknown",
                "token": token,
            },
            status_code=404,
        )

    return templates.TemplateResponse(
        request,
        "operador/scan_result.html",
        {
            "user": user,
            "error": None,
            "payload": payload,
            "booking": booking,
            "fuel_label": fuel_label,
            "fuel_kind": fuel_kind,
            "token": token,
        },
    )


class ConfirmScanBody(BaseModel):
    token: str


@router.post("/operador/api/scan/confirm")
def api_confirm_scan(
    body: ConfirmScanBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_operador_or_coord),
):
    """Confirma producto mostrado → bitácora fuel_scan_log. No habilita pico."""
    try:
        payload = verify_toma_token(body.token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    fuel_label, _kind = _canonical_fuel_for_matricula(db, payload.matricula)
    if fuel_label is None:
        raise HTTPException(
            status_code=404,
            detail="Matrícula sin combustible en el maestro. No se confirma.",
        )

    booking = db.get(Booking, payload.booking_id)
    log = FuelScanLog(
        booking_id=payload.booking_id if booking else None,
        matricula=payload.matricula,
        product_shown=fuel_label,
        confirmed=True,
        user_id=user.id,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return {
        "ok": True,
        "log_id": log.id,
        "matricula": payload.matricula,
        "product_shown": fuel_label,
        "booking_id": payload.booking_id,
        "message": "Producto confirmado. Dejá constancia con la foto de la toma.",
    }
