"""Gestión de usuarios — exclusiva del nivel 2.

Un usuario de nivel 2 puede:
  * dar de alta a alguien por email antes de que entre por primera vez
  * cambiarle el nivel a cualquiera
  * bloquear y desbloquear cuentas

Reglas de seguridad, para no quedarse afuera del propio sistema:
  * nadie puede cambiarse el nivel a sí mismo ni bloquearse
  * no se puede degradar ni bloquear al último nivel 2 que queda
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_user_manager
from app.config import settings
from app.database import get_db
from app.models import ROLE_LABELS, Booking, BookingStatus, Role, User
from app.templating import templates

router = APIRouter(prefix="/admin/usuarios", tags=["admin-usuarios"])

VALID_ROLES = {Role.CLIENTE.value, Role.NIVEL_1.value, Role.NIVEL_2.value}


def _back(message: str = "", error: str = "") -> RedirectResponse:
    query = ""
    if message:
        query = f"?ok={message}"
    elif error:
        query = f"?error={error}"
    return RedirectResponse(f"/admin/usuarios{query}", status_code=303)


def _count_level2(db: Session, *, excluding: int | None = None) -> int:
    query = select(func.count(User.id)).where(
        User.role == Role.NIVEL_2, User.is_blocked.is_(False)
    )
    if excluding is not None:
        query = query.where(User.id != excluding)
    return db.scalar(query) or 0


@router.get("")
def list_users(
    request: Request,
    q: str | None = None,
    ok: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    manager: User = Depends(require_user_manager),
):
    query = select(User)
    if q:
        needle = f"%{q.strip().lower()}%"
        query = query.where(
            func.lower(User.email).like(needle)
            | func.lower(User.name).like(needle)
            | func.lower(User.company).like(needle)
        )

    users = list(db.scalars(query.order_by(User.role.desc(), User.email)).all())

    # Turnos futuros de cada uno: sirve para saber a quién estás por bloquear
    now = datetime.now(UTC)
    counts = dict(
        db.execute(
            select(Booking.user_id, func.count(Booking.id))
            .where(Booking.status == BookingStatus.CONFIRMED, Booking.starts_at > now)
            .group_by(Booking.user_id)
        ).all()
    )

    return templates.TemplateResponse(
        request,
        "admin/usuarios.html",
        {
            "users": users,
            "upcoming": counts,
            "role_labels": ROLE_LABELS,
            "roles": [Role.CLIENTE, Role.NIVEL_1, Role.NIVEL_2],
            "user": manager,
            "q": q or "",
            "ok": ok,
            "error": error,
            "bootstrap_emails": sorted(settings.admin_emails),
        },
    )


@router.post("/crear")
def create_user(
    email: str = Form(...),
    name: str = Form(""),
    role: str = Form(Role.CLIENTE.value),
    db: Session = Depends(get_db),
    manager: User = Depends(require_user_manager),
):
    """Da de alta a alguien antes de su primer ingreso.

    No crea contraseñas: cuando la persona entre con Google usando ese mismo
    email, cae en esta cuenta y ya tiene el nivel asignado.
    """
    email = email.strip().lower()
    if "@" not in email or len(email) < 5:
        return _back(error="Email inválido")

    if role not in VALID_ROLES:
        return _back(error="Nivel inválido")

    if db.scalar(select(User).where(User.email == email)):
        return _back(error="Ya existe un usuario con ese email")

    db.add(User(email=email, name=name.strip()[:160], role=role))
    db.commit()
    return _back(message=f"Usuario {email} creado")


@router.post("/{user_id}/nivel")
def change_role(
    user_id: int,
    role: str = Form(...),
    db: Session = Depends(get_db),
    manager: User = Depends(require_user_manager),
):
    target = db.get(User, user_id)
    if target is None:
        return _back(error="Usuario inexistente")

    if role not in VALID_ROLES:
        return _back(error="Nivel inválido")

    if target.id == manager.id:
        return _back(error="No podés cambiarte el nivel a vos mismo")

    if (
        target.role == Role.NIVEL_2
        and role != Role.NIVEL_2.value
        and _count_level2(db, excluding=target.id) == 0
    ):
        return _back(error="Tiene que quedar al menos un usuario de nivel 2")

    target.role = role
    db.commit()
    return _back(message=f"{target.email} ahora es {ROLE_LABELS.get(role, role)}")


@router.post("/{user_id}/bloqueo")
def toggle_block(
    user_id: int,
    db: Session = Depends(get_db),
    manager: User = Depends(require_user_manager),
):
    target = db.get(User, user_id)
    if target is None:
        return _back(error="Usuario inexistente")

    if target.id == manager.id:
        return _back(error="No podés bloquearte a vos mismo")

    if (
        not target.is_blocked
        and target.role == Role.NIVEL_2
        and _count_level2(db, excluding=target.id) == 0
    ):
        return _back(error="Tiene que quedar al menos un usuario de nivel 2 activo")

    target.is_blocked = not target.is_blocked
    db.commit()

    estado = "bloqueado" if target.is_blocked else "desbloqueado"
    return _back(message=f"{target.email} {estado}")


@router.get("/{user_id}")
def user_detail(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    manager: User = Depends(require_user_manager),
):
    """Ficha del cliente con todos sus datos y su historial de turnos."""
    target = db.get(User, user_id)
    if target is None:
        return _back(error="Usuario inexistente")

    bookings = list(
        db.scalars(
            select(Booking)
            .where(Booking.user_id == target.id)
            .order_by(Booking.starts_at.desc())
            .limit(50)
        ).all()
    )

    now = datetime.now(UTC)
    return templates.TemplateResponse(
        request,
        "admin/usuario_detalle.html",
        {
            "target": target,
            "bookings": bookings,
            "user": manager,
            "now": now,
            "role_labels": ROLE_LABELS,
            "roles": [Role.CLIENTE, Role.NIVEL_1, Role.NIVEL_2],
            "recent": now - timedelta(days=30),
        },
    )
