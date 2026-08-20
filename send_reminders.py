"""Envía los recordatorios de turnos próximos, a mano.

    python send_reminders.py --dry-run    muestra a quién le mandaría, sin enviar
    python send_reminders.py              envía de verdad
    python send_reminders.py --window 6   amplía la ventana de búsqueda (horas)

En Render **no hace falta ejecutarlo**: la app corre esta misma barrida cada hora
en una tarea de fondo (ver app/reminders.py). Con SQLite sobre disco no se puede
usar un Cron Job aparte, porque el disco se monta en un solo servicio.

Este script sigue siendo útil para probar en local, para forzar un envío puntual,
o si algún día migrás a Postgres y querés volver al cron externo.
"""

import argparse
import logging
import sys

from app.reminders import send_due_reminders

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("turnos.reminders.cli")


def main() -> int:
    parser = argparse.ArgumentParser(description="Envía recordatorios de turnos próximos.")
    parser.add_argument("--dry-run", action="store_true", help="no envía ni marca nada")
    parser.add_argument("--window", type=int, default=2, help="ancho de la ventana en horas")
    args = parser.parse_args()

    sent, failed = send_due_reminders(window_hours=args.window, dry_run=args.dry_run)
    logger.info("Listo. Enviados: %s / Fallidos: %s", sent, failed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
