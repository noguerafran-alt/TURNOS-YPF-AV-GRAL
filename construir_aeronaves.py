# -*- coding: utf-8 -*-
r"""Maestro de aviones (.xlsx) -> aeronaves.json, el registro que usa /pico.

    python construir_aeronaves.py "maestro-aviones-version-final.xlsx" salida.json

EL ARCHIVO DE SALIDA NO VA AL REPO. Son matrículas de clientes de YPF y este repo
es público. Va al disco persistente de Render, al lado de turnos.db:

    /var/data/aeronaves.json        (y PICO_REGISTRO apunta ahí)

Para probar en local, generalo en cualquier carpeta fuera del repo y exportá
PICO_REGISTRO con esa ruta.

POR QUÉ UN JSON Y NO UNA TABLA. Son ~2.100 filas de referencia, de solo lectura,
que se regeneran enteras cuando cambia el maestro. Una tabla pediría migración,
modelo y seed para dar exactamente el mismo dict en memoria y búsqueda O(1).

LA NORMALIZACIÓN ES EL CONTRATO. Las claves se guardan normalizadas con la misma
función que usa `app/pico_registro.py` al consultar. Si las dos difieren, no se
encuentra NINGUNA matrícula y todo cae en ABSTENERSE sin que nada se rompa de
forma visible. Por eso este script importa `normalizar` de allá en vez de tener
su propia copia.
"""
import json
import sys
from collections import Counter
from datetime import datetime

import openpyxl

from app.pico_registro import normalizar


def producto(nombre):
    """Combustible -> 'JET' | 'AVGAS' | None si no se puede saber.

    Ambiguo (dice las dos cosas) devuelve None a propósito: caer en la primera
    rama de un `if` clasificaría en silencio, y el silencio es el sentido
    peligroso del error.
    """
    t = (nombre or "").upper()
    jet, avg = "JET" in t, "AVGAS" in t
    return None if jet == avg else ("JET" if jet else "AVGAS")


def construir(origen):
    ws = openpyxl.load_workbook(origen, read_only=True, data_only=True).active
    filas = list(ws.iter_rows(values_only=True))
    col = {str(c).strip(): i for i, c in enumerate(filas[0]) if c}
    for req in ("Combustible", "Matricula"):
        if req not in col:
            sys.exit("Falta la columna %r. Encontradas: %s" % (req, list(col)))

    aeronaves, descartes = {}, Counter()
    for f in filas[1:]:
        mat = normalizar(f[col["Matricula"]])
        prod = producto(f[col["Combustible"]])
        if not mat:
            descartes["sin_matricula"] += 1
            continue
        if prod is None:
            descartes["producto_desconocido"] += 1
            continue
        avion = f[col["Avion"]] if "Avion" in col else None
        avion = str(avion).strip() if avion not in (None, "") else None

        previo = aeronaves.get(mat)
        if previo and previo["producto"] not in (prod, "CONFLICTO"):
            # Dos filas dicen combustibles distintos para la misma matrícula. No se
            # desempata por ningún criterio: es dato sucio o un misfueling histórico,
            # y ninguna de las dos cosas se resuelve en rampa. El interlock bloquea.
            aeronaves[mat] = {"producto": "CONFLICTO", "avion": previo.get("avion") or avion}
        elif not previo:
            aeronaves[mat] = {"producto": prod, "avion": avion}

    return aeronaves, descartes


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    origen, destino = sys.argv[1], sys.argv[2]

    aeronaves, descartes = construir(origen)
    conteo = Counter(a["producto"] for a in aeronaves.values())

    with open(destino, "w", encoding="utf-8") as fh:
        json.dump({"generado": datetime.now().isoformat(timespec="seconds"),
                   "origen": origen, "conteo": dict(conteo), "aeronaves": aeronaves},
                  fh, ensure_ascii=False, sort_keys=True, indent=1)

    print("Escrito: %s" % destino)
    print("Aeronaves unicas: %d" % len(aeronaves))
    for k, n in sorted(conteo.items()):
        print("  %-10s %d" % (k, n))
    print("Descartes: %s" % (dict(descartes) or "ninguno"))

    conflictos = [m for m, a in aeronaves.items() if a["producto"] == "CONFLICTO"]
    if conflictos:
        print("\nEN CONFLICTO (se bloquean, no se desempatan): %d" % len(conflictos))
        for m in conflictos[:10]:
            print("   %s  %s" % (m, aeronaves[m].get("avion")))

    # Chequeo del contrato: toda clave escrita se encuentra a si misma al consultar.
    assert all(normalizar(m) == m for m in aeronaves), "hay claves sin normalizar"
    print("\nOK: las %d claves son estables bajo normalizar()" % len(aeronaves))
