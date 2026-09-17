"""Empresas e invitaciones (fase 1).

- Nivel 2: crear empresa + primer admin
- Admin empresa: invitar por email, listar pendientes/activos
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
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
