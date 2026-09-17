"""Panel de administración: agendas, horarios, cortes y turnos reservados."""

import csv
import io
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.auth import require_admin
from app.config import settings
from app.database import get_db
from app.models import Agenda, Booking, BookingStatus, Closure, MatriculaCombustible, ScheduleRule, User
from app.slots import week_start
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin"])


def _slugify(value: str) -> str:
    value = value.strip().lower()
    replacements = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n", "ü": "u"}
    for old, new in replacements.items():
        value = value.replace(old, new)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:120]


def _get_agenda(db: Session, agenda_id: int) -> Agenda:
    agenda = db.get(Agenda, agenda_id)
    if agenda is None:
        raise HTTPException(status_code=404, detail="Agenda inexistente.")
    return agenda


def _parse_local(value: str) -> datetime:
    """Convierte el valor de un <input type=datetime-local> a UTC."""
    try:
        naive = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Fecha inválida.") from exc
    return naive.replace(tzinfo=settings.tz).astimezone(UTC)


# ============================================================
# Listado general
# ============================================================
@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    agendas = db.scalars(select(Agenda).order_by(Agenda.sort_order, Agenda.name)).all()

    now = datetime.now(UTC)
    week_end = now + timedelta(days=7)

    stats = {
        "agendas": len(agendas),
        "clientes": db.scalar(select(func.count(User.id))),
        "turnos_semana": db.scalar(
            select(func.count(Booking.id)).where(
                Booking.status == BookingStatus.CONFIRMED,
                Booking.starts_at >= now,
                Booking.starts_at < week_end,
            )
        ),
        "turnos_total": db.scalar(
            select(func.count(Booking.id)).where(Booking.status == BookingStatus.CONFIRMED)
        ),
    }

    proximos = db.scalars(
        select(Booking)
        .options(selectinload(Booking.agenda), selectinload(Booking.user))
        .where(Booking.status == BookingStatus.CONFIRMED, Booking.starts_at >= now)
        .order_by(Booking.starts_at)
        .limit(10)
    ).all()

    # Rango por defecto del formulario de exportación: el mes en curso
    today = datetime.now(settings.tz).date()

    matriculas_count = db.scalar(select(func.count(MatriculaCombustible.id))) or 0

    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "agendas": agendas,
            "stats": stats,
            "proximos": proximos,
            "user": admin,
            "export_from": today.replace(day=1).isoformat(),
            "export_to": today.isoformat(),
            "matriculas_count": matriculas_count,
            "import_result": request.query_params.get("import"),
            "import_msg": request.query_params.get("msg"),
        },
    )


# ============================================================
# Alta y edición de agendas
# ============================================================
@router.post("/agendas")
def create_agenda(
    name: str = Form(...),
    product: str = Form(""),
    location: str = Form(""),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    slug = _slugify(f"{name} {product}") or _slugify(name)

    # Si el slug ya existe se le agrega un sufijo numérico
    base_slug, counter = slug, 2
    while db.scalar(select(Agenda.id).where(Agenda.slug == slug)):
        slug = f"{base_slug}-{counter}"
        counter += 1

    agenda = Agenda(slug=slug, name=name.strip(), product=product.strip(), location=location.strip())
    db.add(agenda)
    db.commit()
    db.refresh(agenda)
    return RedirectResponse(f"/admin/agendas/{agenda.id}", status_code=303)


@router.get("/agendas/{agenda_id}")
def agenda_detail(
    agenda_id: int,
    request: Request,
    saved: bool = False,
    error: str | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    agenda = _get_agenda(db, agenda_id)
    now = datetime.now(UTC)

    closures = db.scalars(
        select(Closure)
        .where(Closure.agenda_id == agenda.id, Closure.ends_at >= now - timedelta(days=30))
        .order_by(Closure.starts_at)
    ).all()

    return templates.TemplateResponse(
        request,
        "admin/agenda_detail.html",
        {
            "agenda": agenda,
            "rules": sorted(agenda.rules, key=lambda r: (r.weekday, r.start_time)),
            "closures": closures,
            "saved": saved,
            "error": error,
            "user": admin,
        },
    )


@router.post("/agendas/{agenda_id}")
def agenda_save(
    agenda_id: int,
    name: str = Form(...),
    product: str = Form(""),
    location: str = Form(""),
    address: str = Form(""),
    description: str = Form(""),
    important_info: str = Form(""),
    slot_minutes: int = Form(20),
    capacity: int = Form(1),
    lead_time_hours: int = Form(2),
    horizon_days: int = Form(30),
    cancel_limit_hours: int = Form(2),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    agenda = _get_agenda(db, agenda_id)

    agenda.name = name.strip()
    agenda.product = product.strip()
    agenda.location = location.strip()
    agenda.address = address.strip()
    agenda.description = description.strip()
    agenda.important_info = important_info.strip()
    # Los valores se acotan a rangos sensatos por si alguien edita el HTML
    agenda.slot_minutes = max(5, min(240, slot_minutes))
    agenda.capacity = max(1, min(50, capacity))
    agenda.lead_time_hours = max(0, min(168, lead_time_hours))
    agenda.horizon_days = max(1, min(365, horizon_days))
    agenda.cancel_limit_hours = max(0, min(168, cancel_limit_hours))
    agenda.is_active = is_active

    db.commit()
    return RedirectResponse(f"/admin/agendas/{agenda.id}?saved=1", status_code=303)


# ============================================================
# Franjas horarias
# ============================================================
@router.post("/agendas/{agenda_id}/rules")
def add_rule(
    agenda_id: int,
    weekday: list[int] = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    agenda = _get_agenda(db, agenda_id)

    try:
        start = time.fromisoformat(start_time)
        end = time.fromisoformat(end_time)
    except ValueError:
        return RedirectResponse(
            f"/admin/agendas/{agenda.id}?error=Horario+inválido", status_code=303
        )

    added = 0
    for day in weekday:
        if not 0 <= day <= 6:
            continue
        exists = db.scalar(
            select(ScheduleRule.id).where(
                ScheduleRule.agenda_id == agenda.id,
                ScheduleRule.weekday == day,
                ScheduleRule.start_time == start,
            )
        )
        if exists:
            continue
        db.add(ScheduleRule(agenda_id=agenda.id, weekday=day, start_time=start, end_time=end))
        added += 1

    db.commit()
    if added == 0:
        return RedirectResponse(
            f"/admin/agendas/{agenda.id}?error=Esas+franjas+ya+existían", status_code=303
        )
    return RedirectResponse(f"/admin/agendas/{agenda.id}?saved=1", status_code=303)


@router.post("/agendas/{agenda_id}/rules/{rule_id}/delete")
def delete_rule(
    agenda_id: int,
    rule_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    rule = db.get(ScheduleRule, rule_id)
    if rule and rule.agenda_id == agenda_id:
        db.delete(rule)
        db.commit()
    return RedirectResponse(f"/admin/agendas/{agenda_id}?saved=1", status_code=303)


# ============================================================
# Cortes / feriados
# ============================================================
@router.post("/agendas/{agenda_id}/closures")
def add_closure(
    agenda_id: int,
    starts_at: str = Form(...),
    ends_at: str = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    agenda = _get_agenda(db, agenda_id)
    start = _parse_local(starts_at)
    end = _parse_local(ends_at)

    if end <= start:
        return RedirectResponse(
            f"/admin/agendas/{agenda.id}?error=El+fin+del+corte+debe+ser+posterior+al+inicio",
            status_code=303,
        )

    db.add(Closure(agenda_id=agenda.id, starts_at=start, ends_at=end, reason=reason.strip()[:200]))
    db.commit()
    return RedirectResponse(f"/admin/agendas/{agenda.id}?saved=1", status_code=303)


@router.post("/agendas/{agenda_id}/closures/{closure_id}/delete")
def delete_closure(
    agenda_id: int,
    closure_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    closure = db.get(Closure, closure_id)
    if closure and closure.agenda_id == agenda_id:
        db.delete(closure)
        db.commit()
    return RedirectResponse(f"/admin/agendas/{agenda_id}?saved=1", status_code=303)


# ============================================================
# Turnos reservados
# ============================================================
# ============================================================
# Exportación a CSV
# ============================================================
CSV_HEADERS = [
    "ID", "Aeroplanta", "Sede", "Producto", "Fecha", "Desde", "Hasta", "Estado",
    "Cliente", "Email", "Teléfono", "Empresa", "Matrícula", "Modelo", "N° Vuelo", "Litros",
    "Observaciones", "Reservado el", "Cancelado el", "Cancelado por",
]

def _safe_cell(value) -> str:
    """Neutraliza las celdas que Excel ejecutaría como fórmula.

    Las observaciones y la matrícula las escribe el cliente, así que alguien
    podría cargar `=HYPERLINK("http://malo.com","Factura")` y engañar a quien
    abra el CSV. Anteponer un apóstrofo lo convierte en texto.

    La regla es más fina que "empieza con = + - @" a propósito: un teléfono como
    `+54 9 11 5555-1234` empieza con `+` y no tiene nada de peligroso, y llenarlo
    de apóstrofos ensucia todos los exports. Los ataques reales (HYPERLINK,
    WEBSERVICE, DDE) siempre necesitan `=` o paréntesis.
    """
    if value is None:
        return ""

    text = str(value)
    if not text:
        return text

    dangerous = text[0] in "=@\t\r" or (text[0] in "+-" and ("(" in text or "=" in text))
    return "'" + text if dangerous else text


def _booking_row(booking: Booking) -> list[str]:
    local_start = booking.starts_at.astimezone(settings.tz)
    local_end = booking.ends_at.astimezone(settings.tz)
    agenda = booking.agenda
    user = booking.user

    if booking.status == BookingStatus.CANCELLED:
        cancelled_by = "Aeroplanta" if booking.cancelled_by_admin else "Cliente"
    else:
        cancelled_by = ""

    return [
        _safe_cell(v)
        for v in (
            booking.id,
            agenda.full_name if agenda else "",
            agenda.name if agenda else "",
            agenda.product if agenda else "",
            local_start.strftime("%d/%m/%Y"),
            local_start.strftime("%H:%M"),
            local_end.strftime("%H:%M"),
            "Cancelado" if booking.status == BookingStatus.CANCELLED else "Confirmado",
            user.display_name if user else "",
            user.email if user else "",
            user.phone if user else "",
            user.company if user else "",
            booking.aircraft,
            booking.aircraft_model,
            booking.flight_number,
            booking.liters if booking.liters is not None else "",
            booking.notes,
            booking.created_at.astimezone(settings.tz).strftime("%d/%m/%Y %H:%M")
            if booking.created_at
            else "",
            booking.cancelled_at.astimezone(settings.tz).strftime("%d/%m/%Y %H:%M")
            if booking.cancelled_at
            else "",
            cancelled_by,
        )
    ]


def _csv_stream(bookings: list[Booking]) -> Iterator[str]:
    """Genera el CSV fila por fila, sin armar todo el archivo en memoria."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")

    def flush() -> str:
        value = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return value

    # BOM: sin esto Excel en Windows muestra los acentos rotos
    yield "﻿"

    writer.writerow(CSV_HEADERS)
    yield flush()

    for booking in bookings:
        writer.writerow(_booking_row(booking))
        yield flush()


def _export_range(desde: str | None, hasta: str | None) -> tuple[date, date]:
    """Rango de fechas pedido. Por defecto, la semana actual."""
    today = datetime.now(settings.tz).date()
    try:
        start = date.fromisoformat(desde) if desde else week_start(today)
    except ValueError:
        start = week_start(today)
    try:
        end = date.fromisoformat(hasta) if hasta else start + timedelta(days=6)
    except ValueError:
        end = start + timedelta(days=6)

    if end < start:
        start, end = end, start
    return start, end


def _query_bookings(
    db: Session, *, agenda_id: int | None, start: date, end: date, estado: str
) -> list[Booking]:
    desde_utc = datetime.combine(start, time.min, tzinfo=settings.tz).astimezone(UTC)
    # `end` es inclusivo para el usuario: se suma un día para cubrirlo entero
    hasta_utc = datetime.combine(end + timedelta(days=1), time.min, tzinfo=settings.tz).astimezone(UTC)

    query = (
        select(Booking)
        .options(selectinload(Booking.agenda), selectinload(Booking.user))
        .where(Booking.starts_at >= desde_utc, Booking.starts_at < hasta_utc)
        .order_by(Booking.starts_at, Booking.id)
    )

    if agenda_id is not None:
        query = query.where(Booking.agenda_id == agenda_id)
    if estado == "confirmados":
        query = query.where(Booking.status == BookingStatus.CONFIRMED)
    elif estado == "cancelados":
        query = query.where(Booking.status == BookingStatus.CANCELLED)

    return list(db.scalars(query).all())


def _csv_response(bookings: list[Booking], filename: str) -> StreamingResponse:
    return StreamingResponse(
        _csv_stream(bookings),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )




# ============================================================
# Maestro matrículas × combustible
# ============================================================
@router.post("/matriculas/import")
def import_maestro_matriculas(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Re-importa el xlsx embebido en data/ (o MAESTRO_MATRICULAS_PATH)."""
    from scripts.import_maestro_matriculas import import_candidates, read_candidates, resolve_path

    _ = admin
    try:
        path = resolve_path(None)
        candidates, read_stats = read_candidates(path)
        result = import_candidates(candidates, dry_run=False, replace=True)
    except Exception as exc:  # noqa: BLE001 — feedback al admin
        from urllib.parse import quote
        return RedirectResponse(
            f"/admin?import=error&msg={quote(str(exc)[:180])}",
            status_code=303,
        )

    msg = (
        f"unique={read_stats.get('unique_matriculas')} "
        f"inserted={result['inserted']} updated={result['updated']} "
        f"deleted={result['deleted_before_load']} "
        f"total={result['total_in_db']} replace=1"
    )
    from urllib.parse import quote
    return RedirectResponse(f"/admin?import=ok&msg={quote(msg)}", status_code=303)

@router.get("/turnos.csv")
def export_all_bookings(
    desde: str | None = None,
    hasta: str | None = None,
    estado: str = "todos",
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Todos los turnos de todas las agendas en un rango de fechas."""
    start, end = _export_range(desde, hasta)
    bookings = _query_bookings(db, agenda_id=None, start=start, end=end, estado=estado)
    filename = f"turnos_{start.isoformat()}_a_{end.isoformat()}.csv"
    return _csv_response(bookings, filename)


@router.get("/agendas/{agenda_id}/turnos.csv")
def export_agenda_bookings(
    agenda_id: int,
    desde: str | None = None,
    hasta: str | None = None,
    estado: str = "todos",
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Turnos de una agenda puntual en un rango de fechas."""
    agenda = _get_agenda(db, agenda_id)
    start, end = _export_range(desde, hasta)
    bookings = _query_bookings(db, agenda_id=agenda.id, start=start, end=end, estado=estado)
    filename = f"turnos_{agenda.slug}_{start.isoformat()}_a_{end.isoformat()}.csv"
    return _csv_response(bookings, filename)


@router.get("/agendas/{agenda_id}/turnos")
def agenda_bookings(
    agenda_id: int,
    request: Request,
    d: str | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    agenda = _get_agenda(db, agenda_id)

    try:
        reference = date.fromisoformat(d) if d else datetime.now(settings.tz).date()
    except ValueError:
        reference = datetime.now(settings.tz).date()

    monday = week_start(reference)
    start = datetime.combine(monday, time.min, tzinfo=settings.tz).astimezone(UTC)
    end = start + timedelta(days=7)

    bookings = db.scalars(
        select(Booking)
        .options(selectinload(Booking.user))
        .where(
            Booking.agenda_id == agenda.id,
            Booking.starts_at >= start,
            Booking.starts_at < end,
        )
        .order_by(Booking.starts_at)
    ).all()

    return templates.TemplateResponse(
        request,
        "admin/bookings.html",
        {
            "agenda": agenda,
            "bookings": bookings,
            "monday": monday,
            "sunday": monday + timedelta(days=6),
            "prev_monday": monday - timedelta(days=7),
            "next_monday": monday + timedelta(days=7),
            "user": admin,
        },
    )
