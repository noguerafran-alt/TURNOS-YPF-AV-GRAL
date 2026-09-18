"""Alcance de agendas (aeroplantas) por usuario.

Nivel 2 (admin): sin restricción.
Nivel 1 (coordinador): solo agendas en user_agendas (fail-closed si vacío).
Operador: si tiene filas en user_agendas, se respetan; si no, sin restricción extra
(el panel /operador ya filtra por asignación de turno).
Cliente: no aplica (None = sin filtro en paths de coord).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi import HTTPException, status

from app.models import Agenda, Role, User, UserAgenda

# Mensaje UI cuando un coordinador no tiene plantas asignadas.
AVISO_SIN_AEROPLANTA = (
    "No tenés aeroplantas asignadas. Pedile a un administrador que te asigne "
    "aeroplanta(s) en Maestros → Usuarios."
)


def allowed_agenda_ids(user: User, db: Session) -> set[int] | None:
    """IDs de agenda permitidos.

    Returns:
        None — sin restricción (nivel 2, o operador sin asignaciones).
        set vacío — fail-closed (nivel 1 sin asignaciones).
        set con ids — filtrar por esos agendas.
    """
    if user.role == Role.NIVEL_2:
        return None

    ids = set(
        db.scalars(
            select(UserAgenda.agenda_id).where(UserAgenda.user_id == user.id)
        ).all()
    )

    if user.role == Role.NIVEL_1:
        return ids  # vacío = fail-closed

    if user.role == Role.OPERADOR:
        # Solo restringir si hay asignación explícita; no romper panel existente.
        return ids if ids else None

    return None


def is_scope_empty(user: User, db: Session) -> bool:
    """True si el usuario está restringido y no tiene ninguna agenda."""
    allowed = allowed_agenda_ids(user, db)
    return allowed is not None and len(allowed) == 0


def scoped_agendas(
    db: Session,
    user: User,
    *,
    active_only: bool = True,
) -> list[Agenda]:
    """Lista de agendas visibles para el usuario (ordenadas)."""
    allowed = allowed_agenda_ids(user, db)
    stmt = select(Agenda)
    if active_only:
        stmt = stmt.where(Agenda.is_active.is_(True))
    if allowed is not None:
        if not allowed:
            return []
        stmt = stmt.where(Agenda.id.in_(allowed))
    return list(db.scalars(stmt.order_by(Agenda.sort_order, Agenda.name)).all())


def resolve_agenda_filter(
    user: User,
    db: Session,
    agenda_id: int | None,
) -> int | None | set[int]:
    """Resuelve el filtro de agenda para queries de bookings/maestros.

    Returns:
        None — sin filtro de agenda (admin sin planta elegida).
        int — filtrar por esa agenda (validada).
        set[int] — filtrar por IN (nivel1 con «todas» sus plantas, o vacío = nada).

    Raises:
        HTTPException 403 si agenda_id pedida está fuera de alcance.
    """
    allowed = allowed_agenda_ids(user, db)

    if agenda_id is not None:
        if allowed is not None and agenda_id not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tenés acceso a esa aeroplanta.",
            )
        return agenda_id

    if allowed is None:
        return None  # admin: todas
    return allowed  # set (posiblemente vacío)


def apply_agenda_id_filter(stmt, column, resolved):
    """Aplica el resultado de resolve_agenda_filter a un statement SQLAlchemy."""
    if resolved is None:
        return stmt
    if isinstance(resolved, set):
        if not resolved:
            return stmt.where(False)
        return stmt.where(column.in_(resolved))
    return stmt.where(column == resolved)


def require_agenda_access(user: User, db: Session, agenda_id: int) -> None:
    """403 si el usuario no puede operar sobre esa agenda."""
    allowed = allowed_agenda_ids(user, db)
    if allowed is None:
        return
    if agenda_id not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tenés acceso a esa aeroplanta.",
        )


def require_booking_agenda_access(user: User, db: Session, booking) -> None:
    """403 si el booking pertenece a una agenda fuera de alcance."""
    require_agenda_access(user, db, booking.agenda_id)


def set_user_agendas(db: Session, user: User, agenda_ids: list[int] | None) -> None:
    """Reemplaza las aeroplantas asignadas al usuario (solo ids activos válidos)."""
    existing = list(
        db.scalars(select(UserAgenda).where(UserAgenda.user_id == user.id)).all()
    )
    for link in existing:
        db.delete(link)
    db.flush()

    if not agenda_ids:
        return

    valid = set(
        db.scalars(
            select(Agenda.id).where(
                Agenda.id.in_(set(agenda_ids)),
                Agenda.is_active.is_(True),
            )
        ).all()
    )
    for aid in sorted(valid):
        db.add(UserAgenda(user_id=user.id, agenda_id=aid))


def user_agenda_ids(user: User) -> list[int]:
    """Ids ya cargados en relationship (sin query extra si eager)."""
    return [link.agenda_id for link in (user.agenda_links or [])]


def maestro_plant_scope(column, agenda_id: int | None, allowed: set[int] | None):
    """Filtro para maestros con agenda_id nullable (hangares/abastecedoras/operadores).

    - allowed None: comportamiento histórico (Todas = todo; planta = global OR planta).
    - allowed set: Solo globales + plantas permitidas; planta pedida debe estar en allowed.
    - allowed vacío: solo filas globales (agenda_id IS NULL) cuando agenda_id is None;
      si piden planta → 403 vía resolve previo.
    """
    from sqlalchemy import or_

    if allowed is None:
        if agenda_id is None:
            return None
        return or_(column.is_(None), column == agenda_id)

    if agenda_id is not None:
        if agenda_id not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tenés acceso a esa aeroplanta.",
            )
        return or_(column.is_(None), column == agenda_id)

    # «Todas» restringidas: globales + plantas asignadas
    if not allowed:
        return column.is_(None)
    return or_(column.is_(None), column.in_(allowed))
