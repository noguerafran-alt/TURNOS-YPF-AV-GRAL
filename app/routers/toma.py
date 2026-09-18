# -*- coding: utf-8 -*-
"""Verificación de toma: AVGAS o JET antes de cargar. Operador de planta o coordinación.

El operador elige el equipo con el que va a cargar, tipea la matrícula de la
aeronave que está viendo y saca una foto de su toma de combustible. El sistema
cruza las fuentes que ya existen y, si hay contradicción, TRABA el pico de ese
equipo.

EL CRUCE QUE IMPORTA: lo que la aeronave necesita (maestro, por matrícula)
contra lo que la manguera va a tirar (`Abastecedora.grado`). Hoy `coord.py`
compara el grado de la abastecedora contra lo que el cliente DECLARÓ y contra la
agenda, pero nunca contra el maestro: si el cliente declara AVGAS, la agenda es
AVGAS y la abastecedora es AVGAS, todo pasa aunque el avión sea un turbohélice
que lleva JET. Ese agujero es el que cierra esta pantalla.

REUSA LO QUE YA ESTÁ, a propósito: `require_operador_or_coord` para el permiso,
`lookup_matricula` para el maestro, y `save_toma_upload` + `TomaFoto` para la
foto. Nada de copias propias: dos implementaciones de lo mismo se desincronizan
y la que queda vieja falla en silencio.

POLARIDAD. En reposo no hay traba y el operador trabaja como siempre. El sistema
solo puede AGREGAR una restricción. Si se cae el server o el hotspot, el ESP
libera por watchdog y todo queda como hoy.

EL CICLO. La comparación es por evento (cuando el operador verifica); el
enforcement es en loop (el ESP consulta `/toma/estado` cada pocos segundos).
Recalcular el cruce en loop daría siempre lo mismo: las fuentes no cambian solas.

LA FOTO todavía no se evalúa: es evidencia y dataset. La etiqueta no va en el
nombre del archivo — sale de unir `TomaFoto.matricula` contra el maestro, que es
la fuente de verdad del producto.
"""
import datetime
import hmac
import json
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import toma_traba, toma_veredicto as V
from app.auth import require_operador_or_coord
from app.config import settings
from app.database import get_db
from app.matricula import lookup_matricula, normalize_grado, normalize_matricula
from app.models import Abastecedora, Booking, TomaFoto, User
from app.templating import templates
from app.toma_storage import save_toma_upload

router = APIRouter(prefix="/toma", tags=["toma"])

# El grado tal como lo guarda la app -> el código del núcleo de decisión.
GRADO = {"JET A-1": V.JET, "AVGAS 100LL": V.AVGAS}


def _bitacora_dir() -> Path:
    # Al lado de las fotos, en el disco persistente. Guarda lo que ninguna tabla
    # cubre: veredicto, motivo y si trabó. ponytail: JSONL para no pedir una
    # migración por esto; si hace falta consultarlo, pasa a tabla.
    return Path(settings.toma_storage_dir) / "verificaciones"


def _anotar(id_reg: str, payload: dict) -> str:
    """Rastro append-only, un archivo por día. Devuelve '' si pudo, o el motivo.

    Si no se puede escribir, NO se bloquea la verificación: se devuelve igual el
    veredicto y se le avisa al operador. Perder el rastro en silencio sería peor
    que perderlo a la vista.
    """
    try:
        d = _bitacora_dir() / id_reg[:10]
        d.mkdir(parents=True, exist_ok=True)
        with (d / "bitacora.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        return ""
    except OSError as e:
        return "No se pudo guardar el registro de esta verificación (%s)." % e


@router.get("")
def pantalla(request: Request, db: Session = Depends(get_db),
             user: User = Depends(require_operador_or_coord)):
    equipos = db.query(Abastecedora).filter(Abastecedora.activo.is_(True)) \
                .order_by(Abastecedora.sort_order, Abastecedora.nombre).all()
    return templates.TemplateResponse(
        request, "toma.html",
        {"user": user,
         "equipos": [{"id": a.id, "nombre": a.nombre,
                      "codigo": a.codigo or "", "grado": a.grado} for a in equipos]},
    )


@router.post("/verificar")
async def verificar(
    matricula: str = Form(""),
    abastecedora_id: int | None = Form(None),
    booking_id: int | None = Form(None),
    foto_toma: UploadFile | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_operador_or_coord),
):
    if not matricula.strip():
        return JSONResponse(status_code=400, content=V._r(
            "ABSTENERSE", None, None, "Falta la matrícula.",
            ["Escribí la matrícula de la aeronave que vas a cargar."]))

    if foto_toma is None:
        return JSONResponse(status_code=400, content=V._r(
            "ABSTENERSE", None, None, "Falta la foto de la toma.",
            ["Sacá la foto de la toma de combustible de la aeronave."]))

    clave = normalize_matricula(matricula)
    lk = lookup_matricula(db, matricula)

    # El equipo: sin él no hay cruce con la manguera y no hay a qué trabar.
    ab = db.get(Abastecedora, abastecedora_id) if abastecedora_id else None
    equipo = GRADO.get(normalize_grado(ab.grado)) if ab else None
    codigo = (ab.codigo or "").strip() if ab else ""

    # El turno, si el operador vino desde uno.
    turno = None
    bk = db.get(Booking, booking_id) if booking_id else None
    if bk is not None:
        turno = {"aircraft": normalize_matricula(bk.aircraft or "") or None,
                 "declarado": normalize_grado(bk.combustible_declarado or "") or None}

    if lk.found and not lk.combustible:
        # Conocida pero sin combustible: primera carga. Distinto de "no figura",
        # y decirlo mal manda al operador a buscar donde no es.
        r = V._r("ABSTENERSE", None, clave,
                 "La matrícula %s figura en el maestro pero no tiene combustible asignado." % clave,
                 ["Es una primera carga.",
                  "Verificá el producto contra la placa de la aeronave antes de cargar."],
                 lk.modelo)
    else:
        registro = None
        if lk.found and lk.combustible:
            g = normalize_grado(lk.combustible)
            # Un grado que no reconocemos NO se adivina: decidir() lo manda a rojo.
            registro = {"producto": GRADO.get(g, g), "avion": lk.modelo}
        # confianza "alta": la tipeó una persona. Si se equivocó, el maestro no la
        # encuentra y cae en ABSTENERSE, que es el lado seguro del error.
        r = V.decidir(matricula=clave or None, confianza="alta", registro=registro,
                      equipo=equipo, turno=turno)

    ahora = datetime.datetime.now(settings.tz)
    id_reg = "%s-%s" % (ahora.strftime("%Y-%m-%dT%H:%M:%S"), secrets.token_hex(2))
    r["id_registro"] = id_reg
    r["equipo"] = ab.nombre if ab else ""

    # ---- La traba. Solo el rojo traba, y solo si sabemos a qué equipo.
    # Una verificación sin contradicción sobre el mismo equipo LIBERA: es el
    # camino normal por el que el operador se destraba corrigiendo el error.
    if codigo:
        if r["traba"]:
            toma_traba.trabar(codigo, motivo=r["motivo"], matricula=clave, id_registro=id_reg)
        else:
            toma_traba.liberar(codigo)
    elif r["traba"]:
        r["detalle"] = r["detalle"] + [
            "El equipo no tiene código cargado: no se pudo trabar el pico."]

    # ---- La foto, por el mismo camino que /operador/api/toma-foto.
    # La etiqueta sale de unir matricula contra el maestro, no del nombre.
    digest = ""
    try:
        path_str, digest = await save_toma_upload(matricula=clave, upload=foto_toma)
        db.add(TomaFoto(matricula=clave, booking_id=booking_id, user_id=user.id,
                        path=path_str, sha256=digest))
        db.commit()
    except (ValueError, OSError) as e:
        r["detalle"] = r["detalle"] + ["No se pudo guardar la foto (%s)." % e]

    falla = _anotar(id_reg, {
        "id": id_reg, "momento": ahora.isoformat(), "usuario": user.email,
        "matricula_tipeada": matricula.strip(), "veredicto": r["veredicto"],
        "producto": r["producto"], "matricula": r["matricula"], "modelo": r["modelo"],
        "motivo": r["motivo"], "traba": r["traba"], "equipo": codigo,
        "equipo_grado": ab.grado if ab else "", "booking_id": booking_id,
        "sha256": digest})
    if falla:
        r["detalle"] = r["detalle"] + [falla]

    return JSONResponse(content=r)


@router.get("/estado")
def estado(equipo: str = "", x_toma_token: str = Header(default="")):
    """Lo consulta el ESP cada pocos segundos. Sin sesión: se autentica con token.

    Devuelve `traba` y `segundos` (cuánto le queda). El ESP tiene que aplicar su
    propio watchdog: si no logra consultar durante unos segundos, LIBERA. Que el
    sistema se caiga nunca puede dejar un pico trabado.

    El token viaja sobre HTTPS. Si se filtrara, lo peor que puede hacer un
    tercero es suprimir una traba —o sea, dejar la operación como está hoy— o
    trabar un equipo, que es ruidoso y se nota al toque. La polaridad del sistema
    acota el daño de un token comprometido.
    """
    # compare_digest evita el timing side-channel de "!="; normalizamos a ""
    # porque explota si algún lado es None.
    if not settings.toma_esp_token or not hmac.compare_digest(
        x_toma_token or "", settings.toma_esp_token
    ):
        raise HTTPException(status_code=401, detail="Token inválido.")
    return JSONResponse(content=toma_traba.estado(equipo))


@router.post("/liberar")
def liberar(payload: dict, user: User = Depends(require_operador_or_coord)):
    """Liberación explícita por una persona. Queda en la bitácora con el motivo."""
    codigo = str(payload.get("equipo", ""))
    motivo = str(payload.get("motivo", "")).strip()
    if not codigo or not motivo:
        raise HTTPException(status_code=400, detail="Equipo y motivo son obligatorios.")
    habia = toma_traba.liberar(codigo)
    ahora = datetime.datetime.now(settings.tz)
    _anotar(ahora.strftime("%Y-%m-%dT%H:%M:%S"),
            {"momento": ahora.isoformat(), "usuario": user.email, "equipo": codigo,
             "evento": "TRABA_LIBERADA_POR_PERSONA", "motivo": motivo, "habia_traba": habia})
    return JSONResponse(content={"ok": True, "habia_traba": habia})


@router.post("/confirmar")
def confirmar(payload: dict, user: User = Depends(require_operador_or_coord)):
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
