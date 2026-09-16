"""Importa el maestro matrículas × combustible (xlsx) a matriculas_combustible.

Uso:
  python -m scripts.import_maestro_matriculas
  python -m scripts.import_maestro_matriculas --path /ruta/al.xlsx
  MAESTRO_MATRICULAS_PATH=/ruta.xlsx python -m scripts.import_maestro_matriculas

Por defecto lee data/maestro-matriculas-combustible.xlsx del repo.
Hoja: MATRICULAS Y COMBUSTIBLE.

Reglas:
  - Matricula_Validada (fallback Matricula); normalizar alfanum upper
  - ProductoNombre → JET A-1 / AVGAS 100LL
  - Preferir Estado=OK al resolver duplicados; importar otras filas válidas
  - Upsert por matrícula (sin ownership)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_PATH = ROOT / "data" / "maestro-matriculas-combustible.xlsx"
SHEET_NAME = "MATRICULAS Y COMBUSTIBLE"
ESTADO_PREF = {"OK": 0, "CORREGIDA": 1, "MILITAR": 2, "MATRICULA SIN MODELO": 3}

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("import_maestro")


@dataclass
class Candidate:
    key: str
    display: str
    grado: str
    modelo: str
    estado: str
    score: int


def _estado_rank(estado: str) -> int:
    return ESTADO_PREF.get((estado or "").strip().upper(), 9)


def read_candidates(path: Path) -> tuple[list[Candidate], dict]:
    from openpyxl import load_workbook

    from app.matricula import grado_from_producto_nombre, normalize_matricula

    wb = load_workbook(path, read_only=True, data_only=True)
    if SHEET_NAME not in wb.sheetnames:
        raise SystemExit(f"Hoja '{SHEET_NAME}' no encontrada. Hojas: {wb.sheetnames}")
    ws = wb[SHEET_NAME]
    rows = ws.iter_rows(values_only=True)
    header = [str(c or "").strip() for c in next(rows)]
    expected = [
        "CodigoProducto",
        "ProductoNombre",
        "Matricula",
        "Matricula_Validada",
        "Estado",
        "Modelo_Avion",
        "Avion",
        "Pais_o_Fuerza",
        "Observacion",
    ]
    if header[:9] != expected:
        log.warning("Header inesperado: %s (se usa por posición)", header)

    stats = Counter()
    by_key: dict[str, list[Candidate]] = defaultdict(list)

    for raw in rows:
        stats["rows_raw"] += 1
        producto = str(raw[1] or "")
        mat_raw = raw[3] if raw[3] not in (None, "") else raw[2]
        estado = str(raw[4] or "").strip()
        modelo = str(raw[5] or raw[6] or "").strip()[:80]

        key = normalize_matricula(str(mat_raw or ""))
        if not key:
            stats["skip_empty_matricula"] += 1
            continue

        grado = grado_from_producto_nombre(producto)
        if not grado:
            stats["skip_unknown_grado"] += 1
            log.warning("Grado no mapeado (%s) matrícula=%s", producto.strip(), key)
            continue

        display = str(mat_raw or "").strip().upper() or key
        cand = Candidate(
            key=key,
            display=display[:40],
            grado=grado,
            modelo=modelo,
            estado=estado,
            score=_estado_rank(estado),
        )
        by_key[key].append(cand)
        stats["rows_valid"] += 1

    wb.close()

    chosen: list[Candidate] = []
    for key, cands in by_key.items():
        # Preferir mejor Estado; ante empate, mayoría de grado; luego con modelo
        best_rank = min(c.score for c in cands)
        pool = [c for c in cands if c.score == best_rank]
        if not pool:
            pool = cands
        fuel_counts = Counter(c.grado for c in pool)
        top_fuel, _ = fuel_counts.most_common(1)[0]
        pool_fuel = [c for c in pool if c.grado == top_fuel]
        with_modelo = [c for c in pool_fuel if c.modelo]
        pick = (with_modelo or pool_fuel)[0]
        if len(fuel_counts) > 1:
            stats["fuel_conflicts_resolved"] += 1
        if len(cands) > 1:
            stats["duplicate_matriculas"] += 1
        chosen.append(pick)

    stats["unique_matriculas"] = len(chosen)
    return chosen, dict(stats)


def import_candidates(candidates: list[Candidate], *, dry_run: bool = False) -> dict:
    from sqlalchemy import func, select

    from app.database import SessionLocal
    from app.matricula import upsert_matricula_combustible
    from app.models import MatriculaCombustible

    inserted = updated = 0
    db = SessionLocal()
    try:
        existing = {
            row.matricula: row
            for row in db.scalars(select(MatriculaCombustible)).all()
        }
        for cand in candidates:
            row = existing.get(cand.key)
            if row is None:
                inserted += 1
                if not dry_run:
                    upsert_matricula_combustible(
                        db,
                        raw_matricula=cand.display,
                        combustible=cand.grado,
                        modelo=cand.modelo,
                        activo=True,
                    )
            else:
                changed = (
                    (row.combustible or "") != cand.grado
                    or (row.modelo or "") != (cand.modelo or "")
                    or not row.activo
                    or (row.matricula_display or "") != cand.display
                )
                if changed:
                    updated += 1
                    if not dry_run:
                        upsert_matricula_combustible(
                            db,
                            raw_matricula=cand.display,
                            combustible=cand.grado,
                            modelo=cand.modelo or None,
                            activo=True,
                        )
        if not dry_run:
            db.commit()
        total = db.scalar(select(func.count()).select_from(MatriculaCombustible))
    finally:
        db.close()

    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": len(candidates) - inserted - updated,
        "total_in_db": total if not dry_run else None,
        "dry_run": dry_run,
    }


def resolve_path(cli_path: str | None) -> Path:
    raw = cli_path or os.environ.get("MAESTRO_MATRICULAS_PATH") or str(DEFAULT_PATH)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists():
        raise SystemExit(f"No existe el archivo: {path}")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import maestro matrículas + combustible")
    parser.add_argument("--path", help="Ruta al xlsx (default: data/… o env MAESTRO_MATRICULAS_PATH)")
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
        f"inserted={result['inserted']} updated={result['updated']} "
        f"total_db={result['total_in_db']} dry_run={args.dry_run}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
