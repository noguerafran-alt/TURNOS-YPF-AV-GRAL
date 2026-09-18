"""Autenticación con Google OAuth 2.0 (OpenID Connect) y helpers de sesión.

El flujo es el estándar:
    1. /auth/google           -> redirige a Google
    2. Google pide permiso al usuario
    3. /auth/google/callback  -> Authlib valida el id_token y devuelve el perfil
    4. Se crea/actualiza el User y se guarda su id en la cookie de sesión firmada

En desarrollo, si DEV_LOGIN=true, hay un atajo por email que evita tener que
configurar credenciales de Google para probar la app.
"""

from datetime import UTC, datetime

from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Role, User

GOOGLE_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"

oauth = OAuth()
if settings.google_enabled:
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url=GOOGLE_DISCOVERY,
        client_kwargs={"scope": "openid email profile"},
    )


# ============================================================
# Sesión
# ============================================================
SESSION_USER_KEY = "user_id"


def login_user(request: Request, user: User) -> None:
    request.session[SESSION_USER_KEY] = user.id


def logout_user(request: Request) -> None:
    request.session.pop(SESSION_USER_KEY, None)


def get_current_user(
    request: Request, db: Session = Depends(get_db)
) -> User | None:
    """Usuario logueado, o None. No corta el request: sirve para páginas públicas."""
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        return None

    user = db.get(User, user_id)
    if user is None or user.is_blocked:
        request.session.pop(SESSION_USER_KEY, None)
        return None
    return user


def require_user(user: User | None = Depends(get_current_user)) -> User:
    """Para endpoints de API que exigen sesión."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Necesitás iniciar sesión para reservar un turno.",
        )
    return user


def require_admin(user: User | None = Depends(get_current_user)) -> User:
    """Panel de administración: nivel 1 o nivel 2."""
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Iniciá sesión.")
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu cuenta no tiene permisos de administración.",
        )
    return user


def require_user_manager(user: User | None = Depends(get_current_user)) -> User:
    """Gestión de usuarios: solo nivel 2."""
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Iniciá sesión.")
    if not user.can_manage_users:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo un usuario de nivel 2 puede administrar usuarios.",
        )
    return user


def require_operador(user: User | None = Depends(get_current_user)) -> User:
    """Panel de planta: solo rol operador."""
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Iniciá sesión.")
    if not user.is_operador:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu cuenta no tiene permisos de operador de planta.",
        )
    return user


def require_operador_or_coord(user: User | None = Depends(get_current_user)) -> User:
    """Foto toma / scan: operador de planta o coordinación/admin."""
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Iniciá sesión.")
    if not (user.is_operador or user.is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Se necesita rol de operador o coordinación.",
        )
    return user


def post_login_path(user: User, next_path: str = "/") -> str:
    """Destino post-login. Si next es genérico, operadores van a su panel."""
    if next_path and next_path not in ("/", ""):
        return next_path
    if user.is_operador:
        return "/operador"
    return next_path or "/"


# ============================================================
# Alta / actualización de usuarios
# ============================================================
def upsert_user(
    db: Session,
    *,
    email: str,
    name: str = "",
    picture: str = "",
    google_sub: str | None = None,
) -> User:
    """Busca al usuario por email (o por su id de Google) y lo crea si no existe."""
    email = email.strip().lower()

    user: User | None = None
    if google_sub:
        user = db.scalar(select(User).where(User.google_sub == google_sub))
    if user is None:
        user = db.scalar(select(User).where(User.email == email))

    if user is None:
        user = User(email=email, role=Role.CLIENTE)
        db.add(user)

    # Los datos del proveedor pisan a los guardados (el nombre puede cambiar)
    if name:
        user.name = name
    if picture:
        user.picture = picture
    if google_sub:
        user.google_sub = google_sub

    # ADMIN_EMAILS es el arranque en frío: garantiza que siempre haya al menos un
    # nivel 2 para entrar al panel, incluso con la base recién creada. De ahí en
    # más los niveles se manejan desde /admin/usuarios y NO se pisan en cada login
    # (si no, degradar a alguien desde el panel no tendría efecto).
    if user.email in settings.admin_emails and user.role != Role.NIVEL_2:
        user.role = Role.NIVEL_2

    user.last_login_at = datetime.now(UTC)

    # Invitaciones de empresa: si el email tiene pending, adjuntar al login
    from app.empresa_service import attach_pending_invite

    attach_pending_invite(db, user)

    db.commit()
    db.refresh(user)
    return user
