"""Seed idempotente: hangares + abastecedoras + operadores (maestros Turnera).

Uso:
  python -m scripts.seed_flota_operadores
  python -m scripts.seed_flota_operadores --dry-run

Por defecto lee:
  data/hangares.csv
  data/abastecedoras.csv
  data/operadores.csv

Reglas:
  - Hangares: upsert por codigo (H1, PP…). Estado "Activo" → activo=True.
    agenda_id=NULL (maestro global; visible en Maestros con scope Todas / planta).
  - Abastecedoras: upsert por codigo (AB-01…). Estado "Activa" → activo=True;
    "Fuera de servicio" → activo=False. agenda_id=NULL.
  - Operadores: upsert por nombre normalizado (NFKC + casefold + espacios).
    No crea Users; user_id queda NULL hasta enlazar cuenta role=operador.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_HG = ROOT / "data" / "hangares.csv"
DEFAULT_AB = ROOT / "data" / "abastecedoras.csv"
DEFAULT_OP = ROOT / "data" / "operadores.csv"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("seed_flota")


def normalize_nombre(name: str) -> str:
    s = unicodedata.normalize("NFKC", (name or "").strip())
    s = " ".join(s.split())
    return s.casefold()


def _parse_capacidad(raw: str) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def seed_hangares(db, path: Path, *, dry_run: bool) -> dict:
    from sqlalchemy import select

    from app.models import Hangar

    inserted = updated = unchanged = 0
    existing = {
        (row.codigo or "").strip().upper(): row
        for row in db.scalars(select(Hangar)).all()
        if row.codigo
    }

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    for raw in rows:
        codigo = (raw.get("Codigo") or raw.get("codigo") or raw.get("ID") or "").strip().upper()
        if not codigo:
            log.warning("Fila hangar sin código, skip: %s", raw)
            continue
        nombre = (raw.get("Nombre") or raw.get("nombre") or "").strip()
        if not nombre:
            nombre = codigo
        estado = (raw.get("Estado") or raw.get("estado") or "Activo").strip().casefold()
        activo = estado in {"activo", "activa", ""}
        capacidad = _parse_capacidad(raw.get("Capacidad") or raw.get("capacidad") or "")

        row = existing.get(codigo)
        if row is None:
            inserted += 1
            if not dry_run:
                row = Hangar(
                    codigo=codigo[:40],
                    nombre=nombre[:160],
                    agenda_id=None,
                    capacidad=capacidad,
                    activo=activo,
                )
                db.add(row)
                existing[codigo] = row
            continue

        changed = (
            row.nombre != nombre[:160]
            or row.activo != activo
            or (capacidad is not None and row.capacidad != capacidad)
            or row.agenda_id is not None
        )
        if changed:
            updated += 1
            if not dry_run:
                row.nombre = nombre[:160]
                row.activo = activo
                if capacidad is not None:
                    row.capacidad = capacidad
                row.agenda_id = None
        else:
            unchanged += 1

    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "total_csv": len(rows),
    }


def seed_abastecedoras(db, path: Path, *, dry_run: bool) -> dict:
    from sqlalchemy import select

    from app.matricula import normalize_grado
    from app.models import Abastecedora

    inserted = updated = unchanged = 0
    existing = {
        (row.codigo or "").strip().upper(): row
        for row in db.scalars(select(Abastecedora)).all()
        if row.codigo
    }

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    for i, raw in enumerate(rows):
        codigo = (raw.get("ID") or raw.get("codigo") or "").strip().upper()
        if not codigo:
            log.warning("Fila abastecedora sin ID, skip: %s", raw)
            continue
        nombre = (raw.get("Nombre") or raw.get("nombre") or "").strip()
        grado_raw = (raw.get("Grado") or raw.get("grado") or "").strip()
        grado = normalize_grado(grado_raw) or grado_raw
        capacidad_l = _parse_capacidad(raw.get("Capacidad_L") or raw.get("capacidad_l") or "")
        estado = (raw.get("Estado") or raw.get("estado") or "").strip().casefold()
        activo = estado not in {"fuera de servicio", "inactiva", "inactivo", "fuera"}

        row = existing.get(codigo)
        if row is None:
            inserted += 1
            if not dry_run:
                row = Abastecedora(
                    codigo=codigo,
                    nombre=nombre[:80],
                    grado=grado[:40],
                    capacidad_l=capacidad_l,
                    agenda_id=None,
                    activo=activo,
                    fuera_de_servicio_hasta=None,
                    sort_order=i,
                )
                db.add(row)
                existing[codigo] = row
            continue

        changed = (
            row.nombre != nombre[:80]
            or row.grado != grado[:40]
            or row.capacidad_l != capacidad_l
            or row.activo != activo
        )
        if row.agenda_id is not None:
            changed = True
        if changed:
            updated += 1
            if not dry_run:
                row.nombre = nombre[:80]
                row.grado = grado[:40]
                row.capacidad_l = capacidad_l
                row.activo = activo
                row.agenda_id = None
                row.sort_order = i
        else:
            unchanged += 1

    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "total_csv": len(rows),
    }


def seed_operadores(db, path: Path, *, dry_run: bool) -> dict:
    from sqlalchemy import select

    from app.models import Operador, Role, User

    inserted = updated = unchanged = linked = 0
    existing = {
        row.nombre_norm: row for row in db.scalars(select(Operador)).all()
    }
    op_users = db.scalars(
        select(User).where(User.role == Role.OPERADOR, User.is_blocked.is_(False))
    ).all()
    users_by_norm = {}
    for u in op_users:
        for candidate in (u.name, u.email.split("@")[0] if u.email else ""):
            n = normalize_nombre(candidate)
            if n and n not in users_by_norm:
                users_by_norm[n] = u
    used_user_ids = {
        row.user_id for row in existing.values() if row.user_id is not None
    }

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    for raw in rows:
        nombre = (raw.get("Nombre") or raw.get("nombre") or "").strip().strip('"')
        if not nombre:
            continue
        estado = (raw.get("Estado") or raw.get("estado") or "Activo").strip().casefold()
        activo = estado in {"activo", "activa", ""}
        norm = normalize_nombre(nombre)
        if not norm:
            continue

        row = existing.get(norm)
        link_user = users_by_norm.get(norm)
        new_user_id = None
        if link_user is not None and link_user.id not in used_user_ids:
            new_user_id = link_user.id

        if row is None:
            inserted += 1
            if not dry_run:
                row = Operador(
                    nombre=nombre[:160],
                    nombre_norm=norm,
                    activo=activo,
                    user_id=new_user_id,
                )
                db.add(row)
                existing[norm] = row
                if new_user_id:
                    used_user_ids.add(new_user_id)
                    linked += 1
            continue

        changed = row.nombre != nombre[:160] or row.activo != activo
        if row.user_id is None and new_user_id is not None:
            changed = True
            if not dry_run:
                row.user_id = new_user_id
                used_user_ids.add(new_user_id)
                linked += 1
        if changed:
            updated += 1
            if not dry_run:
                row.nombre = nombre[:160]
                row.activo = activo
        else:
            unchanged += 1

    return {
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "linked_users": linked,
        "total_csv": len(rows),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed hangares + abastecedoras + operadores Turnera"
    )
    parser.add_argument("--hangares", type=Path, default=DEFAULT_HG)
    parser.add_argument("--abastecedoras", type=Path, default=DEFAULT_AB)
    parser.add_argument("--operadores", type=Path, default=DEFAULT_OP)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-migrate", action="store_true")
    parser.add_argument(
        "--skip-hangares",
        action="store_true",
        help="Omitir hangares (p.ej. si data/hangares.csv aún no está)",
    )
    args = parser.parse_args(argv)

    if not args.abastecedoras.exists():
        raise SystemExit(f"No existe: {args.abastecedoras}")
    if not args.operadores.exists():
        raise SystemExit(f"No existe: {args.operadores}")

    hangares_path = args.hangares
    skip_hangares = args.skip_hangares
    if not skip_hangares and not hangares_path.exists():
        log.warning(
            "No existe %s — se omite seed de hangares (usar --hangares PATH o copiar CSV).",
            hangares_path,
        )
        skip_hangares = True

    if not args.skip_migrate:
        from app.migrate import upgrade_database

        upgrade_database()

    from sqlalchemy import func, select

    from app.database import SessionLocal
    from app.models import Abastecedora, Hangar, Operador

    db = SessionLocal()
    hg_stats = None
    try:
        if not skip_hangares:
            hg_stats = seed_hangares(db, hangares_path, dry_run=args.dry_run)
        ab_stats = seed_abastecedoras(db, args.abastecedoras, dry_run=args.dry_run)
        op_stats = seed_operadores(db, args.operadores, dry_run=args.dry_run)
        if not args.dry_run:
            db.commit()
        hg_total = db.scalar(select(func.count()).select_from(Hangar))
        ab_total = db.scalar(select(func.count()).select_from(Abastecedora))
        op_total = db.scalar(select(func.count()).select_from(Operador))
    finally:
        db.close()

    if hg_stats is not None:
        log.info("Hangares: %s", hg_stats)
    else:
        log.info("Hangares: omitted (csv missing or --skip-hangares)")
    log.info("Abastecedoras: %s", ab_stats)
    log.info("Operadores: %s", op_stats)

    parts = []
    if hg_stats is not None:
        parts.append(
            f"hangares csv={hg_stats['total_csv']} "
            f"+{hg_stats['inserted']} ~{hg_stats['updated']} ={hg_stats['unchanged']} "
            f"db={hg_total}"
        )
    else:
        parts.append(f"hangares omitted db={hg_total}")
    parts.append(
        f"abastecedoras csv={ab_stats['total_csv']} "
        f"+{ab_stats['inserted']} ~{ab_stats['updated']} ={ab_stats['unchanged']} "
        f"db={ab_total}"
    )
    parts.append(
        f"operadores csv={op_stats['total_csv']} "
        f"+{op_stats['inserted']} ~{op_stats['updated']} ={op_stats['unchanged']} "
        f"linked={op_stats['linked_users']} db={op_total}"
    )
    print("OK " + "; ".join(parts) + f" dry_run={args.dry_run}")
    print(
        "Nota: agenda_id=NULL en hangares/abastecedoras (maestro global; "
        "visible en Maestros con Planta=Todas / global y también al filtrar una planta)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
