# -*- coding: utf-8 -*-
"""El registro de aeronaves: matrícula -> producto. La autoridad del interlock.

NO VIVE EN EL REPO. Son matrículas de clientes de YPF y este repo es público, así
que el archivo va en el disco persistente de Render (`/var/data/`), al lado de
turnos.db, y la ruta se configura con PICO_REGISTRO. Nada de esto se commitea.

Carga perezosa: si el archivo no está, la app igual arranca y lo que falla es la
pantalla /pico, con BLOQUEAR. Un registro ausente no puede tirar abajo los turnos.
"""
import json
import re
from pathlib import Path

# Tiene que ser IDÉNTICA a la normalización con la que se construyó el archivo.
# Si acá normalizamos distinto que allá, no se encuentra NINGUNA matrícula y todo
# cae en ABSTENERSE sin que nada se rompa de forma visible. Es el peor modo de
# falla del sistema porque parece que anda.
_NO_ALFANUM = re.compile(r"[^0-9A-Za-z]")


def normalizar(m):
    """Matrícula -> clave del registro. 'lv-abc ' -> 'LVABC'.

    Vacío/None -> '' (no matchea nada: el interlock bloquea, no adivina).
    """
    if m is None:
        return ""
    return _NO_ALFANUM.sub("", str(m)).upper()


_cache: dict | None = None
_error: str = ""


def _cargar() -> dict:
    global _cache, _error
    if _cache is not None:
        return _cache
    # Import local a proposito: normalizar() no necesita configuracion, y asi
    # el script que construye el registro puede importarla con solo stdlib.
    from app.config import settings
    try:
        crudo = json.loads(Path(settings.pico_registro).read_text(encoding="utf-8"))
        _cache = crudo.get("aeronaves", crudo)
        _error = ""
    except (OSError, ValueError) as e:
        _cache = {}
        _error = "No se pudo leer el registro de aeronaves (%s)." % e
    return _cache


def disponible() -> bool:
    """False si el registro no se pudo cargar. Sin registro no hay veredicto posible."""
    _cargar()
    return not _error


def por_que_no() -> str:
    _cargar()
    return _error


def cuantas() -> int:
    return len(_cargar())


def buscar(matricula: str | None) -> dict | None:
    """La entrada del registro, o None si la matrícula no figura."""
    clave = normalizar(matricula)
    return _cargar().get(clave) if clave else None


if __name__ == "__main__":
    # Chequeo de la normalización, que es donde el sistema falla en silencio.
    assert normalizar("lv-abc ") == "LVABC"
    assert normalizar("LV.ABC") == "LVABC"
    assert normalizar(" lv abc ") == "LVABC"
    assert normalizar(None) == "" and normalizar("") == ""
    assert buscar(None) is None and buscar("") is None
    print("pico_registro.py: normalizacion OK | registro: %s | %d aeronaves"
          % ("disponible" if disponible() else por_que_no(), cuantas()))
