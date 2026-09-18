"""Lookup / upsert matrícula → combustible.

Normalización alineada al modelo: trim, upper, sin espacios ni guiones.
Upsert SOLO al confirmar ABASTECIDO (carga exitosa), salvo import admin del maestro.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MatriculaCombustible


def normalize_matricula(value: str) -> str:
    """Solo letras y números, en mayúsculas, sin espacios ni separadores."""
    return "".join(c for c in (value or "").upper().strip() if c.isalnum())


def parse_matricula(value: str) -> str:
    """Normaliza y exige matrícula no vacía. Lanza ValueError si inválida."""
    key = normalize_matricula(value)
    if not key:
        raise ValueError("La matrícula solo puede tener letras y números.")
    return key


def normalize_grado(value: str) -> str:
    """Normaliza labels de grado para comparar abastecedora vs turno."""
    s = (value or "").upper().strip()
    s = s.replace("-", " ").replace("_", " ")
    s = " ".join(s.split())
    compact = s.replace(" ", "")
    if compact in {"JETA1", "JETA-1"} or "JET" in s or "AEROKEROSENE" in s:
        return "JET A-1"
    if "AVGAS" in s or "100LL" in s or "100 LL" in s:
        return "AVGAS 100LL"
    return s


def grado_from_producto_nombre(producto: str) -> str | None:
    """Mapea ProductoNombre del maestro → grado interno. None si no se reconoce."""
    s = (producto or "").upper().strip()
    if not s:
        return None
    if "JET" in s or "AEROKEROSENE" in s:
        return "JET A-1"
    if "AVGAS" in s or "100LL" in s or "100 LL" in s:
        return "AVGAS 100LL"
    return None


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
    modelo: str | None = None
    tipo: str | None = None

    def as_dict(self) -> dict:
        # modelo/tipo: Avion del maestro (UI Maestros TIPO + booking aircraft_model)
        modelo = self.modelo or self.tipo
        tipo = self.tipo or self.modelo
        return {
            "ok": True,
            "found": self.found,
            "primera_carga": self.primera_carga,
            "unknown_matricula": self.unknown_matricula,
            "matricula": self.matricula,
            "combustible": self.combustible,
            "modelo": modelo,
            "tipo": tipo,
        }


def lookup_matricula(db: Session, raw: str) -> MatriculaLookup:
    key = normalize_matricula(raw)
    display = key
    if not key:
        return MatriculaLookup(
            found=False,
            primera_carga=True,
            unknown_matricula=True,
            matricula="",
            combustible=None,
            modelo=None,
            tipo=None,
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
            modelo=None,
            tipo=None,
        )

    fuel = (row.combustible or "").strip() or None
    modelo = (getattr(row, "modelo", None) or "").strip() or None
    tipo = (getattr(row, "tipo", None) or "").strip() or None
    if fuel is None:
        # Soft-B: conocida pero sin combustible
        return MatriculaLookup(
            found=True,
            primera_carga=True,
            unknown_matricula=False,
            matricula=row.matricula_display or display or key,
            combustible=None,
            modelo=modelo,
            tipo=tipo,
        )

    return MatriculaLookup(
        found=True,
        primera_carga=False,
        unknown_matricula=False,
        matricula=row.matricula_display or display or key,
        combustible=fuel,
        modelo=modelo,
        tipo=tipo,
    )


def upsert_matricula_combustible(
    db: Session,
    *,
    raw_matricula: str,
    combustible: str,
    modelo: str | None = None,
    tipo: str | None = None,
    activo: bool = True,
) -> MatriculaCombustible:
    """Upsert al listado. En operación normal: SOLO al pasar a ABASTECIDO."""
    key = normalize_matricula(raw_matricula)
    display = key
    fuel = (combustible or "").strip()
    if not key or not fuel:
        raise ValueError("Matrícula y combustible son obligatorios para el upsert.")

    modelo_val = (modelo or "").strip()[:80] if modelo is not None else None
    # Avion del maestro → tipo (columna Maestros TIPO) y modelo
    if tipo is not None and tipo.strip():
        tipo_val = tipo.strip()[:60]
    elif modelo_val:
        tipo_val = modelo_val[:60]
    else:
        tipo_val = None

    row = db.scalar(select(MatriculaCombustible).where(MatriculaCombustible.matricula == key))
    if row is None:
        row = MatriculaCombustible(
            matricula=key,
            matricula_display=display,
            combustible=fuel,
            modelo=modelo_val or "",
            tipo=tipo_val or "",
            activo=activo,
        )
        db.add(row)
    else:
        row.matricula_display = display or row.matricula_display
        row.combustible = fuel
        row.activo = activo
        if modelo_val:
            row.modelo = modelo_val
        if tipo_val:
            row.tipo = tipo_val
    return row
