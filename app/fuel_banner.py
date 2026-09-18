"""Banner de grado de combustible (código de color internacional).

AVGAS 100LL → rojo · JET A-1 → negro.
Usado en plantillas Jinja y como fuente de verdad para el helper JS.
Logos oficiales en /static/img/fuel/ (SVG preferido; PNG para email / fallback).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Audience = Literal["cliente", "staff"]
Variant = Literal["turno", "maestro"]

# Rutas estáticas relativas (templates web / JS)
_LOGO_SVG = {
    "jet": "/static/img/fuel/jet-a1.svg",
    "avgas": "/static/img/fuel/avgas-100ll.svg",
}
_LOGO_PNG = {
    "jet": "/static/img/fuel/jet-a1-oficial.png",
    "avgas": "/static/img/fuel/avgas-100ll-oficial.png",
}

# Alt accesible (espacio en 100 LL según branding oficial)
_LOGO_ALT = {
    "jet": "JET A-1",
    "avgas": "AVGAS 100 LL",
}


def _normalize_grado(value: str) -> str:
    """Copia ligera de matricula.normalize_grado (sin importar SQLAlchemy)."""
    s = (value or "").upper().strip()
    s = s.replace("-", " ").replace("_", " ")
    s = " ".join(s.split())
    compact = s.replace(" ", "")
    if compact in {"JETA1", "JETA-1"} or "JET" in s or "AEROKEROSENE" in s:
        return "JET A-1"
    if "AVGAS" in s or "100LL" in s or "100 LL" in s:
        return "AVGAS 100LL"
    return s


@dataclass(frozen=True)
class FuelBanner:
    """Datos listos para renderizar el banner."""

    kind: str  # avgas | jet | unknown | empty
    label: str
    color_code: str  # ROJO | NEGRO | ""
    css_mod: str  # fuel-banner--avgas | --jet | --unknown
    bg: str
    subline: str
    show: bool
    logo_svg: str = ""  # path relativo o vacío
    logo_png: str = ""
    logo_alt: str = ""


_AVGAS_BG = "#C0392B"
_JET_BG = "#1C1C1C"
_UNKNOWN_BG = "transparent"


def _clean_tipo(tipo: str | None, *, for_cliente: bool) -> str:
    t = (tipo or "").strip()
    if not t:
        return ""
    if for_cliente and t.upper() in {"S/D", "SD", "N/D", "ND", "-", "—"}:
        return ""
    return t


def _clean_part(value: str | None) -> str:
    return (value or "").strip()


def resolve_fuel_kind(combustible: str | None) -> tuple[str, str, str]:
    """→ (kind, label, color_code). kind empty si no hay grado."""
    raw = (combustible or "").strip()
    if not raw:
        return ("", "", "")
    g = _normalize_grado(raw)
    if g == "AVGAS 100LL" or "AVGAS" in g or "100LL" in g.replace(" ", ""):
        return ("avgas", "AVGAS 100LL", "ROJO")
    if g == "JET A-1" or "JET" in g:
        return ("jet", "JET A-1", "NEGRO")
    return ("unknown", g or raw, "")


def build_fuel_banner(
    *,
    combustible: str | None,
    matricula: str | None = None,
    tipo: str | None = None,
    cliente: str | None = None,
    audience: Audience = "cliente",
    variant: Variant = "turno",
    locked_by_maestro: bool = False,
) -> FuelBanner:
    """Arma título + sublínea según handoff UX."""
    kind, label, color_code = resolve_fuel_kind(combustible)
    if not kind:
        return FuelBanner(
            kind="empty",
            label="",
            color_code="",
            css_mod="",
            bg="",
            subline="",
            show=False,
        )

    if kind == "avgas":
        css_mod, bg = "fuel-banner--avgas", _AVGAS_BG
    elif kind == "jet":
        css_mod, bg = "fuel-banner--jet", _JET_BG
    else:
        css_mod, bg = "fuel-banner--unknown", _UNKNOWN_BG

    logo_svg = _LOGO_SVG.get(kind, "")
    logo_png = _LOGO_PNG.get(kind, "")
    logo_alt = _LOGO_ALT.get(kind, label)

    mat = _clean_part(matricula)
    tipo_clean = _clean_tipo(tipo, for_cliente=(audience == "cliente"))
    cli = _clean_part(cliente) if audience == "staff" else ""
    code_txt = f"Código de color internacional: {color_code}" if color_code else ""

    use_maestro = locked_by_maestro or variant == "maestro"
    if use_maestro and mat:
        if audience == "staff":
            tip = f" ({tipo_clean})" if tipo_clean else ""
            head = f"Grado bloqueado por el maestro para {mat}{tip}"
        else:
            tip = f" ({tipo_clean})" if tipo_clean else ""
            head = f"Grado según matrícula {mat}{tip}"
        parts = [head]
        if code_txt:
            parts.append(code_txt)
        subline = " · ".join(parts)
    else:
        parts: list[str] = []
        if mat:
            parts.append(mat)
        if tipo_clean:
            parts.append(tipo_clean)
        if cli:
            parts.append(cli)
        if code_txt:
            parts.append(code_txt)
        subline = " · ".join(parts) if parts else code_txt

    return FuelBanner(
        kind=kind,
        label=label,
        color_code=color_code,
        css_mod=css_mod,
        bg=bg,
        subline=subline,
        show=True,
        logo_svg=logo_svg,
        logo_png=logo_png,
        logo_alt=logo_alt,
    )


def fuel_banner_dict(**kwargs) -> dict:
    """Dict para JSON / JS (misma forma que el helper de front)."""
    b = build_fuel_banner(**kwargs)
    return {
        "show": b.show,
        "kind": b.kind,
        "label": b.label,
        "color_code": b.color_code,
        "css_mod": b.css_mod,
        "bg": b.bg,
        "subline": b.subline,
        "logo_svg": b.logo_svg,
        "logo_png": b.logo_png,
        "logo_alt": b.logo_alt,
    }
