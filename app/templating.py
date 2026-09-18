"""Configuración de Jinja2 y filtros compartidos por todas las plantillas."""

import json
from datetime import datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.config import settings
from app.fuel_banner import build_fuel_banner, fuel_banner_dict

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

MONTHS = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
MONTHS_SHORT = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
DAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def _local(value: datetime) -> datetime:
    """Pasa un datetime UTC a la zona horaria de la empresa."""
    return value.astimezone(settings.tz)


def fmt_time(value: datetime) -> str:
    return _local(value).strftime("%H:%M")


def fmt_date(value) -> str:
    """18/08"""
    return value.strftime("%d/%m")


def fmt_date_long(value) -> str:
    """Martes 18 de agosto de 2026"""
    if isinstance(value, datetime):
        value = _local(value).date()
    return f"{DAYS[value.weekday()]} {value.day} de {MONTHS[value.month - 1]} de {value.year}"


def fmt_datetime(value: datetime) -> str:
    """Martes 18 de agosto de 2026, 15:40"""
    return f"{fmt_date_long(value)}, {fmt_time(value)}"


def fmt_datetime_short(value: datetime) -> str:
    """18/08/2026 15:40 en zona local (settings.tz)."""
    return _local(value).strftime("%d/%m/%Y %H:%M")


def weekday_name(value) -> str:
    return DAYS[value.weekday()]


def month_short(value: int) -> str:
    return MONTHS_SHORT[value - 1]


templates.env.filters["time"] = fmt_time
templates.env.filters["date"] = fmt_date
templates.env.filters["date_long"] = fmt_date_long
templates.env.filters["datetime"] = fmt_datetime
templates.env.filters["datetime_short"] = fmt_datetime_short
templates.env.filters["weekday"] = weekday_name
templates.env.filters["month_short"] = month_short


def _tojson(value) -> Markup:
    return Markup(json.dumps(value, ensure_ascii=False))


templates.env.filters["tojson"] = _tojson

# Disponibles en todas las plantillas sin pasarlas por contexto
templates.env.globals["company_name"] = settings.company_name
templates.env.globals["company_tagline"] = settings.company_tagline
templates.env.globals["support_email"] = settings.support_email
templates.env.globals["build_fuel_banner"] = build_fuel_banner
templates.env.globals["fuel_banner_dict"] = fuel_banner_dict
