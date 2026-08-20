"""Carga datos de ejemplo para probar la app.

    python seed.py

Crea dos agendas con horarios cargados. Es idempotente: si la agenda ya existe
por su slug, no la duplica. No toca usuarios ni turnos existentes.
"""

from datetime import time

from sqlalchemy import select

from app.database import SessionLocal
from app.migrate import upgrade_database
from app.models import Agenda, ScheduleRule

AGENDAS = [
    {
        "slug": "san-fernando-avgas-100ll",
        "name": "San Fernando",
        "product": "AVGAS 100LL",
        "location": "San Fernando, Provincia de Buenos Aires",
        "address": "Aeropuerto de San Fernando",
        "description": "Abastecimiento de aeronaves con AVGAS 100LL en pista.",
        "important_info": (
            "Presentarse 10 minutos antes del horario reservado.\n"
            "Traer la documentación de la aeronave y la orden de compra si opera por cuenta corriente.\n"
            "Ante demoras mayores a 15 minutos el turno se libera automáticamente."
        ),
        "slot_minutes": 20,
        "capacity": 1,
        "lead_time_hours": 2,
        "horizon_days": 30,
        # (día_desde, día_hasta, desde, hasta) — 0 = lunes
        "rules": [(0, 4, time(8, 0), time(20, 0)), (5, 5, time(9, 0), time(18, 0))],
    },
    {
        "slug": "san-fernando-jet-a1",
        "name": "San Fernando",
        "product": "JET A-1",
        "location": "San Fernando, Provincia de Buenos Aires",
        "address": "Aeropuerto de San Fernando",
        "description": "Carga de JET A-1 con dos posiciones simultáneas.",
        "important_info": "Coordinar con torre antes del ingreso a plataforma.",
        "slot_minutes": 30,
        "capacity": 2,
        "lead_time_hours": 3,
        "horizon_days": 45,
        "rules": [(0, 4, time(7, 0), time(21, 0)), (5, 6, time(9, 0), time(17, 0))],
    },
]


def main() -> None:
    # Crea o actualiza el esquema con Alembic antes de insertar nada
    upgrade_database()

    with SessionLocal() as db:
        for data in AGENDAS:
            rules = data.pop("rules")

            existing = db.scalar(select(Agenda).where(Agenda.slug == data["slug"]))
            if existing:
                print(f"= Ya existía: {data['slug']}")
                continue

            agenda = Agenda(**data)
            db.add(agenda)
            db.flush()  # necesita el id para las reglas

            for day_from, day_to, start, end in rules:
                for weekday in range(day_from, day_to + 1):
                    db.add(
                        ScheduleRule(
                            agenda_id=agenda.id,
                            weekday=weekday,
                            start_time=start,
                            end_time=end,
                        )
                    )

            print(f"+ Creada: {agenda.full_name}  ->  /a/{agenda.slug}")

        db.commit()

    print("\nListo. Levantá la app con:  uvicorn app.main:app --reload")


if __name__ == "__main__":
    main()
