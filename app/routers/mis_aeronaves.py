"""Mis Aeronaves — lista personal del cliente (comodidad, no ownership)."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_user
from app.database import get_db
from app.matricula import lookup_matricula, normalize_grado, normalize_matricula, parse_matricula
from app.models import User, UserAircraft
from app.templating import templates

router = APIRouter(tags=["mis-aeronaves"])

GRADOS = ("JET A-1", "AVGAS 100LL")


def _list_for(db: Session, user_id: int) -> list[UserAircraft]:
    return list(
        db.scalars(
            select(UserAircraft)
            .where(UserAircraft.user_id == user_id)
            .order_by(UserAircraft.matricula_display, UserAircraft.matricula)
        ).all()
    )


def _owned(db: Session, user: User, aircraft_id: int) -> UserAircraft | None:
    row = db.get(UserAircraft, aircraft_id)
    if row is None or row.user_id != user.id:
        return None
    return row


def _canon_combustible(raw: str, maestro_fuel: str | None) -> str:
    """Reutiliza grado del maestro si existe; si no, normaliza el ingresado."""
    if maestro_fuel:
        return normalize_grado(maestro_fuel) or maestro_fuel.strip()
    cleaned = (raw or "").strip()
    if not cleaned:
        return ""
    return normalize_grado(cleaned) or cleaned[:80]


@router.get("/perfil/aeronaves")
def mis_aeronaves_page(
    request: Request,
    user: User | None = Depends(get_current_user),
    db: Session = Depends(get_db),
    saved: bool = False,
    error: str | None = None,
    edit: int | None = None,
):
    if user is None:
        return RedirectResponse(
            "/auth/login?" + urlencode({"next": "/perfil/aeronaves"}),
            status_code=303,
        )
    items = _list_for(db, user.id)
    editing = _owned(db, user, edit) if edit else None
    return templates.TemplateResponse(
        request,
        "mis_aeronaves.html",
        {
            "user": user,
            "items": items,
            "editing": editing,
            "grados": GRADOS,
            "saved": saved,
            "error": error,
        },
    )


@router.post("/perfil/aeronaves")
def mis_aeronaves_add(
    request: Request,
    matricula: str = Form(...),
    modelo: str = Form(""),
    tipo: str = Form(""),
    combustible: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    key = parse_matricula(matricula)
    display = key
    if not key:
        return RedirectResponse(
            f"/perfil/aeronaves?{urlencode({'error': 'Ingresá una matrícula válida'})}",
            status_code=303,
        )

    exists = db.scalar(
        select(UserAircraft.id).where(
            UserAircraft.user_id == user.id,
            UserAircraft.matricula == key,
        )
    )
    if exists:
        return RedirectResponse(
            f"/perfil/aeronaves?{urlencode({'error': 'Esa matrícula ya está en tu lista'})}",
            status_code=303,
        )

    lookup = lookup_matricula(db, display)
    fuel = _canon_combustible(combustible, lookup.combustible)
    modelo_val = (modelo or "").strip()[:60] or (lookup.modelo or lookup.tipo or "")[:60]
    tipo_val = (tipo or "").strip()[:60] or modelo_val

    row = UserAircraft(
        user_id=user.id,
        matricula=key,
        matricula_display=lookup.matricula if lookup.found else display,
        modelo=modelo_val,
        tipo=tipo_val,
        combustible=fuel[:80],
    )
    db.add(row)
    db.commit()
    return RedirectResponse("/perfil/aeronaves?saved=1", status_code=303)


@router.post("/perfil/aeronaves/{aircraft_id}")
def mis_aeronaves_edit(
    aircraft_id: int,
    request: Request,
    matricula: str = Form(...),
    modelo: str = Form(""),
    tipo: str = Form(""),
    combustible: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    row = _owned(db, user, aircraft_id)
    if row is None:
        return RedirectResponse(
            f"/perfil/aeronaves?{urlencode({'error': 'No encontramos esa aeronave'})}",
            status_code=303,
        )

    key = parse_matricula(matricula)
    display = key
    if not key:
        return RedirectResponse(
            f"/perfil/aeronaves?{urlencode({'error': 'Ingresá una matrícula válida', 'edit': aircraft_id})}",
            status_code=303,
        )

    clash = db.scalar(
        select(UserAircraft.id).where(
            UserAircraft.user_id == user.id,
            UserAircraft.matricula == key,
            UserAircraft.id != row.id,
        )
    )
    if clash:
        return RedirectResponse(
            f"/perfil/aeronaves?{urlencode({'error': 'Esa matrícula ya está en tu lista', 'edit': aircraft_id})}",
            status_code=303,
        )

    lookup = lookup_matricula(db, display)
    # Si el maestro tiene grado, siempre reutilizarlo; no se pisa el maestro.
    fuel = _canon_combustible(combustible, lookup.combustible)

    row.matricula = key
    row.matricula_display = lookup.matricula if lookup.found else display
    row.modelo = (modelo or "").strip()[:60] or (lookup.modelo or lookup.tipo or "")[:60]
    row.tipo = (tipo or "").strip()[:60] or row.modelo
    row.combustible = fuel[:80]
    db.commit()
    return RedirectResponse("/perfil/aeronaves?saved=1", status_code=303)


@router.post("/perfil/aeronaves/{aircraft_id}/quitar")
def mis_aeronaves_remove(
    aircraft_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Quita de mi lista. NO borra MatriculaCombustible del maestro global."""
    row = _owned(db, user, aircraft_id)
    if row is None:
        return RedirectResponse(
            f"/perfil/aeronaves?{urlencode({'error': 'No encontramos esa aeronave'})}",
            status_code=303,
        )
    db.delete(row)
    db.commit()
    return RedirectResponse("/perfil/aeronaves?saved=1", status_code=303)
