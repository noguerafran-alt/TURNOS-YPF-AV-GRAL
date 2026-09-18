"""Empresas e invitaciones (fase 1).

- Nivel 2: crear empresa + primer admin
- Admin empresa: invitar por email, listar pendientes/activos
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.auth import require_user, require_user_manager
from app.database import get_db
from app.empresa_service import create_empresa, create_empresa_selfserve, invite_member
from app.models import (
    Empresa,
    InvitacionEmpresa,
    InvitacionStatus,
    ROLE_IN_EMPRESA_LABELS,
    RoleInEmpresa,
    User,
)
from app.templating import templates

router = APIRouter(tags=["empresas"])


def _perfil_redirect(message: str = "", error: str = "") -> RedirectResponse:
    params: dict[str, str] = {}
    if message:
        params["empresa_ok"] = message
    if error:
        params["empresa_error"] = error
    q = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"/perfil{q}", status_code=303)


def _admin_redirect(message: str = "", error: str = "") -> RedirectResponse:
    params: dict[str, str] = {}
    if message:
        params["ok"] = message
    if error:
        params["error"] = error
    q = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"/admin/empresas{q}", status_code=303)


# ============================================================
# Admin plataforma (nivel 2)
# ============================================================
@router.get("/admin/empresas")
def admin_empresas(
    request: Request,
    ok: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    manager: User = Depends(require_user_manager),
):
    empresas = list(
        db.scalars(
            select(Empresa)
            .options(selectinload(Empresa.members))
            .order_by(Empresa.nombre)
        ).all()
    )
    return templates.TemplateResponse(
        request,
        "admin/empresas.html",
        {
            "empresas": empresas,
            "user": manager,
            "ok": ok,
            "error": error,
            "role_labels": ROLE_IN_EMPRESA_LABELS,
        },
    )


@router.post("/admin/empresas/crear")
def admin_crear_empresa(
    nombre: str = Form(...),
    admin_email: str = Form(...),
    admin_name: str = Form(""),
    db: Session = Depends(get_db),
    manager: User = Depends(require_user_manager),
):
    try:
        empresa, admin = create_empresa(
            db,
            nombre=nombre,
            admin_email=admin_email,
            admin_name=admin_name,
            created_by=manager,
        )
        db.commit()
    except ValueError as exc:
        return _admin_redirect(error=str(exc))

    return _admin_redirect(
        message=f"Empresa «{empresa.nombre}» creada. Admin: {admin.email}"
    )



# ============================================================
# Cliente self-serve — crear mi empresa
# ============================================================
@router.post("/perfil/empresa/crear")
def perfil_crear_empresa(
    nombre: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Cualquier usuario autenticado sin empresa_id puede crear la suya."""
    try:
        empresa = create_empresa_selfserve(db, user=user, nombre=nombre)
        db.commit()
    except ValueError as exc:
        return _perfil_redirect(error=str(exc))

    return _perfil_redirect(
        message=f"Empresa «{empresa.nombre}» creada. Ya podés invitar por email."
    )


# ============================================================
# Admin empresa — invitaciones desde Mi perfil
# ============================================================
@router.post("/perfil/empresa/invitar")
def perfil_invitar(
    email: str = Form(...),
    role_in_empresa: str = Form(RoleInEmpresa.USUARIO_EMPRESA),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not user.is_admin_empresa or not user.empresa_id:
        return _perfil_redirect(error="Solo el admin de la empresa puede invitar.")

    empresa = db.get(Empresa, user.empresa_id)
    if empresa is None or not empresa.activo:
        return _perfil_redirect(error="No encontramos tu empresa.")

    try:
        invite = invite_member(
            db,
            empresa=empresa,
            email=email,
            invited_by=user,
            role_in_empresa=role_in_empresa,
        )
        db.commit()
    except ValueError as exc:
        return _perfil_redirect(error=str(exc))

    if invite.status == InvitacionStatus.ACCEPTED:
        return _perfil_redirect(message=f"{invite.email} quedó vinculado a la empresa.")
    return _perfil_redirect(
        message=f"Invitación enviada a {invite.email}. Se activa al entrar con Google."
    )


@router.post("/perfil/empresa/invitaciones/{invite_id}/cancelar")
def perfil_cancelar_invite(
    invite_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not user.is_admin_empresa or not user.empresa_id:
        return _perfil_redirect(error="Solo el admin de la empresa puede cancelar.")

    invite = db.get(InvitacionEmpresa, invite_id)
    if invite is None or invite.empresa_id != user.empresa_id:
        return _perfil_redirect(error="Invitación inexistente.")
    if invite.status != InvitacionStatus.PENDING:
        return _perfil_redirect(error="Esa invitación ya no está pendiente.")

    invite.status = InvitacionStatus.CANCELLED
    db.commit()
    return _perfil_redirect(message=f"Invitación a {invite.email} cancelada.")


@router.post("/perfil/empresa/invitaciones/{invite_id}/aceptar")
def perfil_aceptar_invite(
    invite_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Acepta una invitación pendiente dirigida al email del usuario."""
    from datetime import UTC, datetime

    from app.models import Empresa, RoleInEmpresa

    if user.empresa_id:
        return _perfil_redirect(error="Ya pertenecés a una empresa.")
    invite = db.get(InvitacionEmpresa, invite_id)
    if (
        invite is None
        or invite.status != InvitacionStatus.PENDING
        or (invite.email or "").strip().lower() != (user.email or "").strip().lower()
    ):
        return _perfil_redirect(error="No encontramos esa invitación.")
    empresa = db.get(Empresa, invite.empresa_id)
    if empresa is None or not empresa.activo:
        return _perfil_redirect(error="La empresa ya no está activa.")
    role = invite.role_in_empresa or RoleInEmpresa.USUARIO_EMPRESA
    if role not in (RoleInEmpresa.ADMIN_EMPRESA, RoleInEmpresa.USUARIO_EMPRESA):
        role = RoleInEmpresa.USUARIO_EMPRESA
    user.empresa_id = empresa.id
    user.role_in_empresa = role
    user.company = empresa.nombre
    invite.status = InvitacionStatus.ACCEPTED
    invite.accepted_at = datetime.now(UTC)
    db.commit()
    return _perfil_redirect(message=f"Te uniste a «{empresa.nombre}».")


@router.post("/perfil/empresa/invitaciones/{invite_id}/rechazar")
def perfil_rechazar_invite(
    invite_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    invite = db.get(InvitacionEmpresa, invite_id)
    if (
        invite is None
        or invite.status != InvitacionStatus.PENDING
        or (invite.email or "").strip().lower() != (user.email or "").strip().lower()
    ):
        return _perfil_redirect(error="No encontramos esa invitación.")
    invite.status = InvitacionStatus.CANCELLED
    db.commit()
    return _perfil_redirect(message="Invitación rechazada.")


@router.post("/perfil/empresa/salir")
def perfil_salir_empresa(
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Renuncia a la membresía formal. No borra la Empresa.

    Si el usuario es el último admin_empresa, se bloquea para no dejar
    la org sin administrador.
    """
    if not user.empresa_id:
        return _perfil_redirect(error="No pertenecés a ninguna empresa.")

    empresa = db.get(Empresa, user.empresa_id)
    if empresa is None:
        # Membresía huérfana: limpiar igual
        user.empresa_id = None
        user.role_in_empresa = None
        db.commit()
        return _perfil_redirect(message="Ya no figurás en ninguna empresa.")

    if user.is_admin_empresa:
        otros_admins = db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.empresa_id == empresa.id,
                User.role_in_empresa == RoleInEmpresa.ADMIN_EMPRESA,
                User.id != user.id,
            )
        )
        if not otros_admins:
            return _perfil_redirect(
                error="Nominá otro administrador antes de salir."
            )

    nombre = empresa.nombre
    user.empresa_id = None
    user.role_in_empresa = None
    db.commit()
    return _perfil_redirect(
        message=f"Saliste de «{nombre}». Podés crear tu empresa o aceptar una invitación."
    )
