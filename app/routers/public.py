"""Páginas públicas: listado de agendas, calendario semanal y 'Mis turnos'."""

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models import Agenda, Booking, BookingStatus, User, UserAircraft
from app.slots import build_week
from app.templating import templates

router = APIRouter(tags=["público"])


def _parse_day(raw: str | None) -> date:
    """Fecha pedida por querystring (?d=2026-08-18). Si es inválida, hoy."""
    today = datetime.now(settings.tz).date()
    if not raw:
        return today
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return today


@router.get("/")
def home(request: Request, db: Session = Depends(get_db), user: User | None = Depends(get_current_user)):
    agendas = db.scalars(
        select(Agenda)
        .where(Agenda.is_active.is_(True))
        .order_by(Agenda.sort_order, Agenda.name)
    ).all()

    return templates.TemplateResponse(
        request, "index.html", {"agendas": agendas, "user": user}
    )


@router.get("/a/{slug}")
def agenda_page(
    slug: str,
    request: Request,
    d: str | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    agenda = db.scalar(select(Agenda).where(Agenda.slug == slug))
    if agenda is None or (not agenda.is_active and not (user and user.is_admin)):
        raise HTTPException(status_code=404, detail="No encontramos esa agenda.")

    today = datetime.now(settings.tz).date()
    selected_day = _parse_day(d)
    # No arrancar antes de hoy (BA): días pasados fuera de la grilla.
    if selected_day < today:
        selected_day = today
    grid_start = selected_day

    week = build_week(db, agenda, grid_start, user_id=user.id if user else None)

    # Navegación: flechas = día a día; mes ant/sig = ±30 días.
    horizon = today + timedelta(days=agenda.horizon_days)
    prev_day = selected_day - timedelta(days=1)
    next_day = selected_day + timedelta(days=1)
    prev_month = selected_day - timedelta(days=30)
    next_month = selected_day + timedelta(days=30)
    if prev_month < today:
        prev_month = today

    mis_aeronaves = []
    if user is not None:
        mis_aeronaves = list(
            db.scalars(
                select(UserAircraft)
                .where(UserAircraft.user_id == user.id)
                .order_by(UserAircraft.matricula_display, UserAircraft.matricula)
            ).all()
        )

    return templates.TemplateResponse(
        request,
        "agenda.html",
        {
            "agenda": agenda,
            "week": week,
            "monday": grid_start,  # inicio de grilla (compat template)
            "sunday": grid_start + timedelta(days=6),
            "selected_day": selected_day,
            "today": today,
            "user": user,
            # Reutilizamos nombres prev/next_monday = día anterior/siguiente
            "prev_monday": prev_day if prev_day >= today else None,
            "next_monday": next_day if next_day <= horizon else None,
            "prev_month": prev_month if prev_month <= horizon else None,
            "next_month": next_month if next_month <= horizon else selected_day,
            "max_liters": 20000,
            "mis_aeronaves": mis_aeronaves,
        },
    )


@router.get("/mis-turnos")
def my_bookings(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    if user is None:
        return RedirectResponse("/auth/login?next=/mis-turnos", status_code=303)

    now = datetime.now(UTC)

    bookings = db.scalars(
        select(Booking)
        .options(selectinload(Booking.agenda))
        .where(Booking.user_id == user.id)
        .order_by(Booking.starts_at.desc())
    ).all()

    upcoming = [
        b for b in bookings if b.status == BookingStatus.CONFIRMED and b.starts_at > now
    ]
    upcoming.reverse()  # el más próximo primero
    history = [b for b in bookings if b not in upcoming]

    return templates.TemplateResponse(
        request,
        "mis_turnos.html",
        {"upcoming": upcoming, "history": history, "user": user, "now": now},
    )
