"""Helpers de Empresa / invitaciones (fase 1)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Empresa,
    InvitacionEmpresa,
    InvitacionStatus,
    MatriculaCombustible,
    RoleInEmpresa,
    User,
)
from app.matricula import normalize_matricula


def _norm_name(value: str) -> str:
    return " ".join((value or "").casefold().split())


def attach_pending_invite(db: Session, user: User) -> InvitacionEmpresa | None:
    """Si el email tiene invitación pending, liga al usuario a la empresa y la acepta.

    Idempotente: si ya está en esa empresa, solo marca la invitación aceptada.
    No mueve a alguien que ya pertenece a otra empresa activa.
    """
    email = (user.email or "").strip().lower()
    if not email:
        return None

    invite = db.scalar(
        select(InvitacionEmpresa)
        .where(
            InvitacionEmpresa.email == email,
            InvitacionEmpresa.status == InvitacionStatus.PENDING,
        )
        .order_by(InvitacionEmpresa.created_at.desc())
    )
    if invite is None:
        return None

    empresa = db.get(Empresa, invite.empresa_id)
    if empresa is None or not empresa.activo:
        return None

    if user.empresa_id and user.empresa_id != invite.empresa_id:
        # Ya pertenece a otra org: no reasignar en silencio
        return None

    role = invite.role_in_empresa or RoleInEmpresa.USUARIO_EMPRESA
    if role not in (RoleInEmpresa.ADMIN_EMPRESA, RoleInEmpresa.USUARIO_EMPRESA):
        role = RoleInEmpresa.USUARIO_EMPRESA

    user.empresa_id = empresa.id
    user.role_in_empresa = role
    user.company = empresa.nombre

    invite.status = InvitacionStatus.ACCEPTED
    invite.accepted_at = datetime.now(UTC)
    return invite


def create_empresa(
    db: Session,
    *,
    nombre: str,
    admin_email: str,
    admin_name: str = "",
    created_by: User | None = None,
) -> tuple[Empresa, User]:
    """Crea empresa + asigna primer admin (crea User si no existe)."""
    nombre = " ".join(nombre.strip().split())
    if not nombre:
        raise ValueError("El nombre de la empresa es obligatorio.")
    if len(nombre) > 160:
        raise ValueError("Nombre demasiado largo.")

    admin_email = admin_email.strip().lower()
    if "@" not in admin_email or len(admin_email) < 5:
        raise ValueError("Email del admin inválido.")

    exists = db.scalar(
        select(Empresa).where(func.lower(Empresa.nombre) == nombre.casefold())
    )
    if exists:
        raise ValueError("Ya existe una empresa con ese nombre.")

    empresa = Empresa(nombre=nombre, activo=True)
    db.add(empresa)
    db.flush()

    user = db.scalar(select(User).where(User.email == admin_email))
    if user is None:
        user = User(email=admin_email, name=(admin_name or "").strip()[:160])
        db.add(user)
        db.flush()
    elif admin_name.strip() and not user.name:
        user.name = admin_name.strip()[:160]

    user.empresa_id = empresa.id
    user.role_in_empresa = RoleInEmpresa.ADMIN_EMPRESA
    user.company = empresa.nombre

    # Registro de invitación ya aceptada (auditoría / coherencia con el flujo)
    invite = InvitacionEmpresa(
        email=admin_email,
        empresa_id=empresa.id,
        invited_by=created_by.id if created_by else None,
        status=InvitacionStatus.ACCEPTED,
        role_in_empresa=RoleInEmpresa.ADMIN_EMPRESA,
        token=secrets.token_urlsafe(24),
        accepted_at=datetime.now(UTC),
    )
    db.add(invite)
    return empresa, user


def invite_member(
    db: Session,
    *,
    empresa: Empresa,
    email: str,
    invited_by: User,
    role_in_empresa: str = RoleInEmpresa.USUARIO_EMPRESA,
) -> InvitacionEmpresa:
    email = email.strip().lower()
    if "@" not in email or len(email) < 5:
        raise ValueError("Email inválido.")

    if role_in_empresa not in (RoleInEmpresa.ADMIN_EMPRESA, RoleInEmpresa.USUARIO_EMPRESA):
        role_in_empresa = RoleInEmpresa.USUARIO_EMPRESA

    existing_user = db.scalar(select(User).where(User.email == email))
    if existing_user and existing_user.empresa_id == empresa.id:
        raise ValueError("Esa persona ya es miembro de la empresa.")
    if existing_user and existing_user.empresa_id and existing_user.empresa_id != empresa.id:
        raise ValueError("Esa persona ya pertenece a otra empresa.")

    pending = db.scalar(
        select(InvitacionEmpresa).where(
            InvitacionEmpresa.empresa_id == empresa.id,
            InvitacionEmpresa.email == email,
            InvitacionEmpresa.status == InvitacionStatus.PENDING,
        )
    )
    if pending:
        raise ValueError("Ya hay una invitación pendiente para ese email.")

    # Si el usuario ya existe y no tiene empresa, adjuntar al instante
    if existing_user and not existing_user.empresa_id:
        existing_user.empresa_id = empresa.id
        existing_user.role_in_empresa = role_in_empresa
        existing_user.company = empresa.nombre
        invite = InvitacionEmpresa(
            email=email,
            empresa_id=empresa.id,
            invited_by=invited_by.id,
            status=InvitacionStatus.ACCEPTED,
            role_in_empresa=role_in_empresa,
            token=secrets.token_urlsafe(24),
            accepted_at=datetime.now(UTC),
        )
        db.add(invite)
        return invite

    invite = InvitacionEmpresa(
        email=email,
        empresa_id=empresa.id,
        invited_by=invited_by.id,
        status=InvitacionStatus.PENDING,
        role_in_empresa=role_in_empresa,
        token=secrets.token_urlsafe(24),
    )
    db.add(invite)
    return invite


def flag_matricula_otra_empresa(
    db: Session,
    *,
    user: User,
    raw_matricula: str,
) -> bool:
    """Best-effort: True si el maestro tiene cliente y no coincide con la empresa del booker.

    Sin dato de ownership en el maestro → False (permitir sin warning).
    """
    if not user.empresa_id:
        return False

    empresa = user.empresa or db.get(Empresa, user.empresa_id)
    if empresa is None:
        return False

    key = normalize_matricula(raw_matricula)
    if not key:
        return False

    row = db.scalar(select(MatriculaCombustible).where(MatriculaCombustible.matricula == key))
    if row is None:
        return False

    owner = (row.cliente or "").strip()
    if not owner:
        # Sin ownership en maestro: no flaggeamos
        return False

    return _norm_name(owner) != _norm_name(empresa.nombre)
