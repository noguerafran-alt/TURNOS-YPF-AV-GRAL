"""Importa el maestro matrículas × combustible (xlsx) a matriculas_combustible.

Uso:
  python -m scripts.import_maestro_matriculas
  python -m scripts.import_maestro_matriculas --path /ruta/al.xlsx
  MAESTRO_MATRICULAS_PATH=/ruta.xlsx python -m scripts.import_maestro_matriculas

Por defecto lee data/maestro-aviones-version-final.xlsx (canónico).
Alternativa versionada: data/maestro-matriculas-combustible.xlsx.
Hojas soportadas (en orden): BASE FINAL, BASE, MATRICULAS Y COMBUSTIBLE.

Reglas (schema final 2105):
  - Columnas: CodigoProducto | Combustible | Matricula | Avion
  - Matricula as-is + normalize (alfanum upper) como clave
  - Combustible → JET A-1 / AVGAS 100LL (también acepta ProductoNombre legacy)
  - FULL REPLACE: borra filas previas de matriculas_combustible y carga el archivo
    (idempotente). Upserts runtime (ABASTECIDO) quedan fuera del maestro hasta
    el próximo import; el archivo es la fuente de verdad del listado.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_PATH = ROOT / "data" / "maestro-aviones-version-final.xlsx"
SHEET_CANDIDATES = ("BASE FINAL", "BASE", "MATRICULAS Y COMBUSTIBLE")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("import_maestro")


@dataclass
class Candidate:
    key: str
    display: str
    grado: str
    modelo: str


def _header_index(header: list[str]) -> dict[str, int]:
    return {h: i for i, h in enumerate(header) if h}


def _cell(row: tuple, idx: dict[str, int], *names: str):
    for name in names:
        i = idx.get(name)
        if i is not None and i < len(row) and row[i] not in (None, ""):
            return row[i]
    return None


def read_candidates(path: Path) -> tuple[list[Candidate], dict]:
    from openpyxl import load_workbook

    from app.matricula import grado_from_producto_nombre, normalize_matricula

    wb = load_workbook(path, read_only=True, data_only=True)
    sheet = next((s for s in SHEET_CANDIDATES if s in wb.sheetnames), None)
    if sheet is None:
        raise SystemExit(
            f"Ninguna hoja conocida {SHEET_CANDIDATES}. Hojas: {wb.sheetnames}"
        )
    ws = wb[sheet]
    rows = ws.iter_rows(values_only=True)
    header = [str(c or "").strip() for c in next(rows)]
    idx = _header_index(header)
    log.info("Hoja=%s header=%s", sheet, header)

    stats: dict = {
        "sheet": sheet,
        "rows_raw": 0,
        "rows_valid": 0,
        "skip_empty_matricula": 0,
        "skip_unknown_grado": 0,
        "duplicate_matriculas": 0,
    }
    by_key: dict[str, Candidate] = {}
    fuel_counts: Counter = Counter()

    for raw in rows:
        stats["rows_raw"] += 1
        combustible_raw = _cell(raw, idx, "Combustible", "ProductoNombre")
        mat_raw = _cell(raw, idx, "Matricula", "Matricula_Validada")
        if combustible_raw is None and len(raw) > 1 and not idx:
            combustible_raw = raw[1]
        if mat_raw is None and len(raw) > 2 and not idx:
            mat_raw = raw[2]
        avion = _cell(raw, idx, "Avion", "Modelo_Avion")
        if (
            avion is None
            and len(raw) > 3
            and "Avion" not in idx
            and "Modelo_Avion" not in idx
        ):
            avion = raw[3] if len(raw) > 3 else None

        key = normalize_matricula(str(mat_raw or ""))
        if not key:
            stats["skip_empty_matricula"] += 1
            continue

        grado = grado_from_producto_nombre(str(combustible_raw or ""))
        if not grado:
            stats["skip_unknown_grado"] += 1
            log.warning(
                "Grado no mapeado (%s) matrícula=%s",
                str(combustible_raw or "").strip(),
                key,
            )
            continue

        display = str(mat_raw or "").strip().upper() or key
        modelo = str(avion or "").strip()[:80]
        cand = Candidate(
            key=key,
            display=display[:40],
            grado=grado,
            modelo=modelo,
        )
        if key in by_key:
            stats["duplicate_matriculas"] += 1
            continue
        by_key[key] = cand
        fuel_counts[grado] += 1
        stats["rows_valid"] += 1

    wb.close()

    chosen = list(by_key.values())
    stats["unique_matriculas"] = len(chosen)
    stats["jet"] = fuel_counts.get("JET A-1", 0)
    stats["avgas"] = fuel_counts.get("AVGAS 100LL", 0)
    return chosen, stats


def import_candidates(candidates: list[Candidate], *, dry_run: bool = False) -> dict:
    """FULL REPLACE: truncate matriculas_combustible then insert all from file."""
    from sqlalchemy import delete, func, select

    from app.database import SessionLocal
    from app.models import MatriculaCombustible

    fuel_counts = Counter(c.grado for c in candidates)
    db = SessionLocal()
    try:
        existing_count = (
            db.scalar(select(func.count()).select_from(MatriculaCombustible)) or 0
        )
        if dry_run:
            return {
                "mode": "full_replace",
                "would_delete": existing_count,
                "would_insert": len(candidates),
                "jet": fuel_counts.get("JET A-1", 0),
                "avgas": fuel_counts.get("AVGAS 100LL", 0),
                "total_in_db": None,
                "dry_run": True,
            }

        deleted = db.execute(delete(MatriculaCombustible)).rowcount or 0
        for cand in candidates:
            db.add(
                MatriculaCombustible(
                    matricula=cand.key,
                    matricula_display=cand.display,
                    combustible=cand.grado,
                    modelo=cand.modelo or "",
                    activo=True,
                )
            )
        db.commit()
        total = db.scalar(select(func.count()).select_from(MatriculaCombustible))
    finally:
        db.close()

    return {
        "mode": "full_replace",
        "deleted": deleted,
        "inserted": len(candidates),
        "jet": fuel_counts.get("JET A-1", 0),
        "avgas": fuel_counts.get("AVGAS 100LL", 0),
        "total_in_db": total,
        "dry_run": False,
    }


def resolve_path(cli_path: str | None) -> Path:
    raw = cli_path or os.environ.get("MAESTRO_MATRICULAS_PATH") or str(DEFAULT_PATH)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists():
        alt = ROOT / "data" / "maestro-matriculas-combustible.xlsx"
        if not cli_path and not os.environ.get("MAESTRO_MATRICULAS_PATH") and alt.exists():
            log.warning("Canónico ausente (%s); uso %s", path, alt)
            return alt.resolve()
        raise SystemExit(f"No existe el archivo: {path}")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import maestro matrículas + combustible")
    parser.add_argument(
        "--path",
        help="Ruta al xlsx (default: data/maestro-aviones-version-final.xlsx)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Solo cuenta, no escribe")
    args = parser.parse_args(argv)

    path = resolve_path(args.path)
    log.info("Leyendo %s", path)
    candidates, read_stats = read_candidates(path)
    log.info("Lectura: %s", read_stats)
    result = import_candidates(candidates, dry_run=args.dry_run)
    log.info("Import: %s", result)
    print(
        f"OK unique={read_stats.get('unique_matriculas')} "
        f"JET={read_stats.get('jet')} AVGAS={read_stats.get('avgas')} "
        f"mode={result.get('mode')} "
        f"deleted={result.get('deleted', result.get('would_delete'))} "
        f"inserted={result.get('inserted', result.get('would_insert'))} "
        f"total_db={result['total_in_db']} dry_run={args.dry_run}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
