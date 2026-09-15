"""Lookup / upsert matrícula → combustible.

Normalización alineada al modelo: trim, upper, sin espacios ni guiones.
Upsert SOLO al confirmar ABASTECIDO (carga exitosa).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MatriculaCombustible


def normalize_matricula(value: str) -> str:
    return "".join(c for c in (value or "").upper().strip() if c.isalnum())


def normalize_grado(value: str) -> str:
    """Normaliza labels de grado para comparar abastecedora vs turno."""
    s = (value or "").upper().strip()
    s = s.replace("-", " ").replace("_", " ")
    s = " ".join(s.split())
    compact = s.replace(" ", "")
    if compact in {"JETA1", "JETA-1"} or "JET" in s:
        return "JET A-1"
    if "AVGAS" in s or "100LL" in s:
        return "AVGAS 100LL"
    return s


def grados_compatibles(a: str, b: str) -> bool:
    na, nb = normalize_grado(a), normalize_grado(b)
    if not na or not nb:
        return False
    return na == nb


@dataclass(frozen=True)
class MatriculaLookup:
    found: bool
    primera_carga: bool
    unknown_matricula: bool
    matricula: str
    combustible: str | None

    def as_dict(self) -> dict:
        return {
            "ok": True,
            "found": self.found,
            "primera_carga": self.primera_carga,
            "unknown_matricula": self.unknown_matricula,
            "matricula": self.matricula,
            "combustible": self.combustible,
        }


def lookup_matricula(db: Session, raw: str) -> MatriculaLookup:
    display = (raw or "").strip().upper()
    key = normalize_matricula(raw)
    if not key:
        return MatriculaLookup(
            found=False,
            primera_carga=True,
            unknown_matricula=True,
            matricula=display,
            combustible=None,
        )

    row = db.scalar(
        select(MatriculaCombustible).where(
            MatriculaCombustible.matricula == key,
            MatriculaCombustible.activo.is_(True),
        )
    )
    if row is None:
        return MatriculaLookup(
            found=False,
            primera_carga=True,
            unknown_matricula=True,
            matricula=display or key,
            combustible=None,
        )

    fuel = (row.combustible or "").strip() or None
    if fuel is None:
        # Soft-B: conocida pero sin combustible
        return MatriculaLookup(
            found=True,
            primera_carga=True,
            unknown_matricula=False,
            matricula=row.matricula_display or display or key,
            combustible=None,
        )

    return MatriculaLookup(
        found=True,
        primera_carga=False,
        unknown_matricula=False,
        matricula=row.matricula_display or display or key,
        combustible=fuel,
    )


def upsert_matricula_combustible(
    db: Session, *, raw_matricula: str, combustible: str
) -> MatriculaCombustible:
    """Upsert al listado. Llamar SOLO al pasar a ABASTECIDO."""
    key = normalize_matricula(raw_matricula)
    display = (raw_matricula or "").strip().upper() or key
    fuel = (combustible or "").strip()
    if not key or not fuel:
        raise ValueError("Matrícula y combustible son obligatorios para el upsert.")

    row = db.scalar(select(MatriculaCombustible).where(MatriculaCombustible.matricula == key))
    if row is None:
        row = MatriculaCombustible(
            matricula=key,
            matricula_display=display,
            combustible=fuel,
            activo=True,
        )
        db.add(row)
    else:
        row.matricula_display = display or row.matricula_display
        row.combustible = fuel
        row.activo = True
    return row
