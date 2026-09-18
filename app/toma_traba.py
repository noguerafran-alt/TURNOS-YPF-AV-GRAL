# -*- coding: utf-8 -*-
"""Estado de la traba por equipo de abastecimiento. Lo consulta el ESP.

CÓMO FUNCIONA EL CICLO. La comparación NO corre en loop: las tres fuentes
(maestro, turno, abastecedora) no cambian solas, así que recalcular cada 2 s
daría siempre lo mismo. Lo que corre en loop es el ENFORCEMENT: el ESP pregunta
"¿mi equipo está trabado?" cada pocos segundos y actúa el solenoide.
Evento para decidir, loop para sostener.

POLARIDAD. En reposo no hay traba: el pico está libre y el operador trabaja como
siempre. Solo una contradicción la activa. Por eso perder este estado —reinicio,
deploy, caída— es seguro: se vuelve a "como hoy", que es el comportamiento que
el sistema nunca debe empeorar.

CÓMO SE LIBERA, en orden de lo que va a pasar de verdad:
  1. El operador corrige y vuelve a verificar. Una verificación sin contradicción
     sobre el mismo equipo pisa la traba. Es el camino normal.
  2. Liberación explícita de una persona, con motivo, que queda en la bitácora.
  3. Vencimiento. Backstop para que una traba olvidada no deje un equipo
     inutilizado toda la noche.
  4. El override físico del ESP, que no pasa por acá y siempre gana.

ponytail: estado en memoria de proceso. Vale porque render.yaml fija
numInstances 1 y un solo worker, y porque perderlo es el lado seguro. Si algún
día hay más de una instancia, esto pasa a una tabla o a Redis; el síntoma de no
hacerlo sería una traba que se ve desde una instancia y no desde la otra.
"""
import datetime
import threading

from app.config import settings

# Cuánto dura una traba sin que nadie la toque. Corto a propósito: si el
# operador se fue, el equipo tiene que volver a estar disponible.
MINUTOS = 30

_lock = threading.Lock()
_trabas: dict[str, dict] = {}


def _ahora():
    return datetime.datetime.now(settings.tz)


def clave(codigo: str) -> str:
    """El código de la abastecedora, normalizado. Es la identidad del ESP."""
    return (codigo or "").strip().upper()


def trabar(codigo: str, *, motivo: str, matricula: str = "", id_registro: str = "") -> dict:
    k = clave(codigo)
    if not k:
        raise ValueError("Falta el código del equipo.")
    ahora = _ahora()
    with _lock:
        _trabas[k] = {"motivo": motivo, "matricula": matricula, "id_registro": id_registro,
                      "desde": ahora, "vence": ahora + datetime.timedelta(minutes=MINUTOS)}
        return dict(_trabas[k])


def liberar(codigo: str) -> bool:
    """Saca la traba. True si habia una."""
    k = clave(codigo)
    with _lock:
        return _trabas.pop(k, None) is not None


def estado(codigo: str) -> dict:
    """Lo que el ESP necesita saber. Sin equipo conocido, no hay traba."""
    k = clave(codigo)
    if not k:
        return {"traba": False, "motivo": "", "segundos": 0}
    ahora = _ahora()
    with _lock:
        t = _trabas.get(k)
        if t is None:
            return {"traba": False, "motivo": "", "segundos": 0}
        if ahora >= t["vence"]:
            del _trabas[k]
            return {"traba": False, "motivo": "", "segundos": 0}
        return {"traba": True, "motivo": t["motivo"], "matricula": t["matricula"],
                "id_registro": t["id_registro"],
                "segundos": int((t["vence"] - ahora).total_seconds())}


def listar() -> dict:
    """Trabas vigentes, para mostrarlas en coordinación."""
    ahora = _ahora()
    with _lock:
        for k in [k for k, t in _trabas.items() if ahora >= t["vence"]]:
            del _trabas[k]
        return {k: dict(t) for k, t in _trabas.items()}


if __name__ == "__main__":
    assert clave(" ab-01 ") == "AB-01"
    assert estado("AB-01")["traba"] is False
    assert estado("")["traba"] is False

    trabar("ab-01", motivo="La aeronave lleva JET A-1 y la abastecedora es AVGAS 100LL.",
           matricula="LVGQL")
    e = estado("AB-01")
    assert e["traba"] is True and e["matricula"] == "LVGQL" and 0 < e["segundos"] <= MINUTOS * 60
    # El codigo se normaliza en los dos lados: el ESP puede mandarlo como venga.
    assert estado(" ab-01 ")["traba"] is True
    # Un equipo distinto no queda trabado por arrastre.
    assert estado("AB-02")["traba"] is False

    assert liberar("AB-01") is True
    assert liberar("AB-01") is False
    assert estado("AB-01")["traba"] is False

    # Vencida: se limpia sola y el equipo vuelve a estar libre.
    trabar("AB-03", motivo="x")
    with _lock:
        _trabas["AB-03"]["vence"] = _ahora() - datetime.timedelta(seconds=1)
    assert estado("AB-03")["traba"] is False
    assert "AB-03" not in listar()

    print("toma_traba.py: 11 checks OK")
