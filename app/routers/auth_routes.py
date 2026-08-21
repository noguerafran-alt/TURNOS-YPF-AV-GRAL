"""Rutas de login / logout / perfil."""

from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user, login_user, logout_user, oauth, require_user, upsert_user
from app.config import settings
from app.database import get_db
from app.models import User
from app.templating import templates

router = APIRouter(tags=["auth"])


def _safe_next(raw: str | None) -> str:
    """Evita open redirects: solo se aceptan rutas internas."""
    if not raw:
        return "/"
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc or not raw.startswith("/"):
        return "/"
    return raw


@router.get("/auth/login")
def login_page(request: Request, next: str | None = None, error: str | None = None):
    user = request.session.get("user_id")
    if user:
        return RedirectResponse(_safe_next(next), status_code=303)

    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "next": _safe_next(next),
            "error": error,
            "google_enabled": settings.google_enabled,
            "dev_login": settings.dev_login and not settings.is_production,
        },
    )


@router.get("/auth/google")
async def google_login(request: Request, next: str | None = None):
    if not settings.google_enabled:
        return RedirectResponse(
            "/auth/login?error=Google+no+está+configurado+en+este+servidor", status_code=303
        )

    request.session["oauth_next"] = _safe_next(next)
    redirect_uri = f"{settings.base_url}/auth/google/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/google/callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception:  # firma inválida, state vencido, usuario canceló…
        return RedirectResponse(
            "/auth/login?error=No+pudimos+validar+tu+cuenta+de+Google.+Probá+de+nuevo.",
            status_code=303,
        )

    claims = token.get("userinfo") or {}
    email = claims.get("email")
    if not email:
        return RedirectResponse(
            "/auth/login?error=Tu+cuenta+de+Google+no+expuso+un+email.", status_code=303
        )
    if claims.get("email_verified") is False:
        return RedirectResponse(
            "/auth/login?error=Tu+email+de+Google+no+está+verificado.", status_code=303
        )

    user = upsert_user(
        db,
        email=email,
        name=claims.get("name", ""),
        picture=claims.get("picture", ""),
        google_sub=claims.get("sub"),
    )
    login_user(request, user)

    destination = request.session.pop("oauth_next", "/")
    return RedirectResponse(_safe_next(destination), status_code=303)


@router.post("/auth/dev-login")
def dev_login(
    request: Request,
    email: str = Form(...),
    name: str = Form(""),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    """Atajo SOLO para desarrollo (DEV_LOGIN=true y ENVIRONMENT != production)."""
    if not settings.dev_login or settings.is_production:
        return RedirectResponse("/auth/login?error=Login+de+desarrollo+deshabilitado", status_code=303)

    user = upsert_user(db, email=email, name=name or email.split("@")[0])
    login_user(request, user)
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/auth/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/", status_code=303)


@router.get("/perfil")
def profile_page(
    request: Request,
    user: User | None = Depends(get_current_user),
    saved: bool = False,
    error: str | None = None,
    next: str | None = None,
):
    if user is None:
        # Se preserva next para no perder el destino (ej: la agenda donde
        # querían reservar) mientras el usuario hace el login. Va con urlencode:
        # es un next anidado dentro de otro next, y sin encodear el "?" y "="
        # internos rompen el parseo de la query string externa.
        login_next = "/perfil" + (f"?{urlencode({'next': next})}" if next else "")
        return RedirectResponse(f"/auth/login?{urlencode({'next': login_next})}", status_code=303)
    return templates.TemplateResponse(
        request, "perfil.html", {"user": user, "saved": saved, "error": error, "next": next}
    )


@router.post("/perfil")
def profile_save(
    request: Request,
    phone: str = Form(...),
    company: str = Form(...),
    name: str = Form(""),
    next: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    # Teléfono y empresa son obligatorios: son los datos que la aeroplanta
    # necesita para contactar a quien reserva. min_length en el form no alcanza
    # solo (dejaría pasar "   "), así que se valida el string ya recortado.
    phone = phone.strip()
    company = company.strip()
    if not phone or not company:
        params = {"error": "Completá tu teléfono y tu empresa antes de guardar"}
        if next:
            params["next"] = next
        return RedirectResponse(f"/perfil?{urlencode(params)}", status_code=303)

    user.phone = phone[:40]
    user.company = company[:160]
    if name.strip():
        user.name = name.strip()[:160]
    db.commit()

    # Si vino de "necesito completar mi perfil para reservar", lo mandamos
    # directo de vuelta a esa agenda en vez de dejarlo varado en /perfil.
    if next:
        return RedirectResponse(_safe_next(next), status_code=303)
    return RedirectResponse("/perfil?saved=1", status_code=303)
