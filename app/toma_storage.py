"""Persistencia de fotos de toma en disco Render (/var/data) o local data/."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.config import settings

ALLOWED_CT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def toma_root() -> Path:
    root = Path(settings.toma_storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


async def save_toma_upload(*, matricula: str, upload: UploadFile) -> tuple[str, str]:
    """Guarda archivo. Retorna (path_relativo_o_absoluto_str, sha256)."""
    ct = (upload.content_type or "").split(";")[0].strip().lower()
    ext = ALLOWED_CT.get(ct)
    if ext is None:
        # fallback por nombre
        name = (upload.filename or "").lower()
        if name.endswith(".jpg") or name.endswith(".jpeg"):
            ext = ".jpg"
        elif name.endswith(".png"):
            ext = ".png"
        elif name.endswith(".webp"):
            ext = ".webp"
        else:
            raise ValueError("Solo se aceptan JPG, PNG o WebP.")

    data = await upload.read()
    if not data:
        raise ValueError("El archivo está vacío.")
    if len(data) > MAX_BYTES:
        raise ValueError("La foto supera el máximo de 10 MB.")

    digest = hashlib.sha256(data).hexdigest()
    folder = toma_root() / matricula
    folder.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}{ext}"
    dest = folder / fname
    dest.write_bytes(data)
    # Guardamos path absoluto estable para Render
    return str(dest), digest

def booking_has_toma_foto(db, booking_id: int) -> bool:
    """True si hay ≥1 foto de toma asociada al turno."""
    from sqlalchemy import select
    from app.models import TomaFoto

    return (
        db.scalar(select(TomaFoto.id).where(TomaFoto.booking_id == booking_id).limit(1))
        is not None
    )


def toma_foto_counts_by_booking(db, booking_ids: list[int]) -> dict[int, int]:
    """booking_id → cantidad de fotos (solo ids con al menos una)."""
    if not booking_ids:
        return {}
    from sqlalchemy import func, select
    from app.models import TomaFoto

    rows = db.execute(
        select(TomaFoto.booking_id, func.count())
        .where(TomaFoto.booking_id.in_(booking_ids))
        .group_by(TomaFoto.booking_id)
    ).all()
    return {int(bid): int(n) for bid, n in rows if bid is not None}

