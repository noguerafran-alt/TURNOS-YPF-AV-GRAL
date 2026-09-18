# -*- coding: utf-8 -*-
"""Verificación de pico: AVGAS o JET antes de cargar. Nivel operario o más.

El operador tipea la matrícula, saca una foto de la TOMA DE COMBUSTIBLE DE LA
AERONAVE y la pantalla dice qué producto corresponde. **Esto no habilita el
pico.** Devuelve un veredicto y deja constancia; habilitar lo hace la persona,
mirando la placa de la aeronave.

SIN SERVICIOS EXTERNOS. La matrícula la tipea el operador —cinco caracteres, más
confiable que cualquier OCR— y el registro decide. Determinístico, gratis y
funciona sin internet.

QUÉ HACE LA FOTO HOY: es la evidencia de la carga, y nada más. No se evalúa
automáticamente todavía.
QUÉ VA A HACER: cada foto se guarda ETIQUETADA con el producto que dictó el
registro, así que cada verificación genera un ejemplo rotulado sin que nadie
etiquete a mano. Cuando haya unos cientos se entrena un clasificador de FORMA
de la toma (chica con anillo restrictor = AVGAS; ancha y en "D" = turbina) y se
enciende el cruce en `pico_veredicto.decidir(placard=...)`, que ya lo espera.
No se puede clasificar por TAMAÑO: la diferencia es 2,3" contra 2,6"
(FAA AC 20-122A), 7 mm sobre 60 mm, y no hay escala en una foto de celular.

Quién entra: `require_admin`, que en este proyecto ya significa nivel 1
(Operador) o nivel 2 — ver `User.is_admin` en models.py. No hace falta un rol
nuevo: el que existe es exactamente "operario o más".
"""
import datetime
import hashlib
import json
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from app import pico_registro, pico_veredicto as V
from app.auth import require_admin
from app.config import settings
from app.models import User
from app.templating import templates

router = APIRouter(prefix="/pico", tags=["pico"])

# La bitácora va al lado del registro, o sea en el disco persistente de Render.
# Nunca en el repo.
BITACORA = Path(settings.pico_registro).parent / "pico_registros"


def _anotar(id_reg: str, payload: dict) -> str:
    """Rastro append-only, un archivo por día. Devuelve '' si pudo, o el motivo.

    Si no se puede escribir, NO se bloquea la verificación: se devuelve igual el
    veredicto y se le avisa al operador en pantalla. Perder el rastro en silencio
    sería peor que perderlo a la vista.
    """
    try:
        d = BITACORA / id_reg[:10]
        d.mkdir(parents=True, exist_ok=True)
        with (d / "bitacora.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        return ""
    except OSError as e:
        return "No se pudo guardar el registro de esta verificación (%s)." % e


@router.get("")
def pantalla(request: Request, user: User = Depends(require_admin)):
    return templates.TemplateResponse(
        request, "pico.html",
        {"user": user, "registro_ok": pico_registro.disponible(),
         "registro_motivo": pico_registro.por_que_no(),
         "registro_cuantas": pico_registro.cuantas()},
    )


@router.post("/verificar")
def verificar(
    matricula: str = Form(""),
    foto_toma: UploadFile | None = None,
    user: User = Depends(require_admin),
):
    if not matricula.strip():
        return JSONResponse(status_code=400, content=V._r(
            "ABSTENERSE", None, None, "Falta la matrícula.",
            ["Escribí la matrícula de la aeronave que vas a cargar."]))

    if not foto_toma:
        return JSONResponse(status_code=400, content=V._r(
            "ABSTENERSE", None, None, "Falta la foto de la toma.",
            ["Sacá la foto de la toma de combustible de la aeronave."]))

    # Sin registro no hay autoridad contra la cual verificar: no se opina.
    if not pico_registro.disponible():
        return JSONResponse(status_code=503, content=V._r(
            "BLOQUEAR", None, None, pico_registro.por_que_no(),
            ["El sistema no puede verificar. Verificación manual."]))

    b = foto_toma.file.read()
    ahora = datetime.datetime.now(settings.tz)
    id_reg = "%s-%s" % (ahora.strftime("%Y-%m-%dT%H:%M:%S"), secrets.token_hex(2))

    mat = pico_registro.normalizar(matricula)
    # confianza "alta": la tipeó una persona. Si se equivocó, el registro no la
    # encuentra y cae en ABSTENERSE, que es el lado seguro del error.
    r = V.decidir(matricula=mat or None, confianza="alta",
                  registro=pico_registro.buscar(mat))
    r["id_registro"] = id_reg

    falla = _anotar(id_reg, {
        "id": id_reg, "momento": ahora.isoformat(), "usuario": user.email,
        "matricula_tipeada": matricula.strip(), "veredicto": r["veredicto"],
        "producto": r["producto"], "matricula": r["matricula"],
        "modelo": r["modelo"], "motivo": r["motivo"],
        "sha256": {"toma": hashlib.sha256(b).hexdigest()}})

    if falla:
        r["detalle"] = r["detalle"] + [falla]
    else:
        # El producto va en el NOMBRE del archivo: así cada foto queda etiquetada
        # por el registro y el dataset se arma solo. Sin producto (ámbar o rojo)
        # queda SIN-ETIQUETA y no sirve para entrenar, que es lo correcto.
        try:
            etiqueta = (r["producto"] or "SIN-ETIQUETA").replace(" ", "-")
            nombre = "%s_toma_%s.jpg" % (id_reg.replace(":", ""), etiqueta)
            (BITACORA / id_reg[:10] / nombre).write_bytes(b)
        except OSError:
            r["detalle"] = r["detalle"] + ["No se pudo guardar la foto de esta verificación."]

    return JSONResponse(content=r)


@router.post("/confirmar")
def confirmar(payload: dict, user: User = Depends(require_admin)):
    """Deja constancia de que una PERSONA confirmó el producto y habilitó.

    El veredicto verde solo propone. Esta línea es la que dice que alguien decidió.
    """
    id_reg = str(payload.get("id_registro", ""))
    if len(id_reg) < 19 or id_reg[4] != "-":
        return JSONResponse(status_code=400, content={"ok": False})
    _anotar(id_reg, {"id": id_reg, "usuario": user.email,
                     "momento": datetime.datetime.now(settings.tz).isoformat(),
                     "evento": "CONFIRMADO_POR_OPERADOR"})
    return JSONResponse(content={"ok": True})
