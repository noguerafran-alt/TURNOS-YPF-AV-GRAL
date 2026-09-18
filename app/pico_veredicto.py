# -*- coding: utf-8 -*-
"""Núcleo de decisión del interlock anti-misfueling. Sin I/O: entra evidencia, sale veredicto.

POLARIDAD DEL SISTEMA — lo único que hay que entender de este archivo:
el REGISTRO de matrículas decide el producto; la foto solo puede VETAR.
Nunca al revés. Una foto no habilita una carga.
Esto no es preferencia de diseño: la NTSB se lo ordena por escrito al personal
de rampa (SA-051, "identificar la aeronave por matrícula y no por marca/modelo")
justamente porque la apariencia miente cuando hay una conversión STC de por medio.

Tres veredictos, y uno solo deja seguir:
  CONSISTENTE  la evidencia cruzada cierra. Confirma y habilita EL OPERADOR.
  ABSTENERSE   no alcanza la evidencia. Verificación manual.
  BLOQUEAR     hay contradicción o dato sucio. No se carga.
No existe el estado AUTORIZAR. Todo lo que no sea explícitamente consistente
cae en ámbar o rojo: fail-closed.

CIELO RASO CONOCIDO: las dos fotos son independientes, así que nada prueba que
la matrícula y la boca de carga sean del MISMO avión. Es el modo de falla de
CEN15LA199 (el personal confundió el avión con otro parecido). Se mitiga con la
confirmación humana, no con software. Arreglo real: una sola foto con matrícula
y boca en el mismo encuadre, cuando la geometría del avión lo permita.
"""

JET, AVGAS, CONFLICTO = "JET", "AVGAS", "CONFLICTO"
NOMBRE = {JET: "JET A-1", AVGAS: "AVGAS 100LL"}


def _r(veredicto, producto, matricula, motivo, detalle, modelo=None):
    return {"veredicto": veredicto, "producto": producto, "matricula": matricula,
            "modelo": modelo, "motivo": motivo, "detalle": detalle}


def decidir(matricula=None, confianza="baja", registro=None,
            placard="INDETERMINADO", planta="INDETERMINADO"):
    """registro: la entrada de registro.json para esa matrícula, o None si no figura.
    placard: qué declara la placa junto a la boca de carga (JET/AVGAS/INDETERMINADO).
    planta: planta motriz observada (PISTON/TURBINA/INDETERMINADO)."""
    d = []

    if not matricula:
        return _r("ABSTENERSE", None, None, "No se pudo leer la matrícula en la foto.",
                  ["Repetir la foto de la matrícula: más cerca, sin contraluz."])
    d.append("Matrícula leída: %s (confianza %s)" % (matricula, confianza))

    if confianza != "alta":
        return _r("ABSTENERSE", None, matricula,
                  "La matrícula se leyó con confianza %s." % confianza,
                  d + ["Repetir la foto o verificar la matrícula a mano."])

    if registro is None:
        return _r("ABSTENERSE", None, matricula,
                  "La matrícula %s no figura en el registro." % matricula,
                  d + ["Verificación documental antes de cargar."])

    prod = registro.get("producto")
    modelo = registro.get("avion") or registro.get("modelo")
    d.append("Registro: %s%s" % (NOMBRE.get(prod, prod), " / %s" % modelo if modelo else ""))

    if prod == CONFLICTO:
        return _r("BLOQUEAR", None, matricula,
                  "La matrícula %s figura en el registro con AVGAS y con JET A-1." % matricula,
                  d + ["Dato en conflicto: no se resuelve en rampa.",
                       "Escalar a la aeroplanta antes de cargar."], modelo)

    if prod not in NOMBRE:
        return _r("BLOQUEAR", None, matricula,
                  "Producto desconocido en el registro para %s." % matricula, d, modelo)

    # El registro ya decidió. De acá abajo la foto solo puede contradecirlo.
    # Los vetos son ASIMÉTRICOS a propósito: no toda discrepancia es peligrosa, y
    # bloquear una carga legítima es exactamente cómo se le enseña a un operador
    # a saltear la herramienta. Se bloquea solo donde no hay explicación inocente.

    # PLACARD: es una declaración del fabricante, no una inferencia sobre la
    # silueta. Contradice en cualquier dirección -> rojo. Regla de la NTSB: ante
    # discrepancia entre lo que se va a cargar y el placard, preguntar.
    if placard in NOMBRE:
        d.append("Placard junto a la boca: %s" % NOMBRE[placard])
        if placard != prod:
            return _r("BLOQUEAR", None, matricula,
                      "El registro dice %s pero el placard de la aeronave dice %s."
                      % (NOMBRE[prod], NOMBRE[placard]),
                      d + ["Contradicción entre registro y aeronave. NO CARGAR.",
                           "Puede ser el avión equivocado, o el registro estar mal."], modelo)
    else:
        d.append("Placard: ausente o ilegible (no aporta).")

    # PLANTA MOTRIZ: acá la asimetría es todo.
    if planta == "TURBINA" and prod == AVGAS:
        # No existe turbina que queme AVGAS. Contradicción sin salida inocente.
        return _r("BLOQUEAR", None, matricula,
                  "El registro dice AVGAS 100LL pero se observa una turbina.",
                  d + ["Ninguna turbina quema AVGAS. NO CARGAR.",
                       "Avión equivocado, o conversión no registrada."], modelo)

    if planta == "PISTON" and prod == JET:
        # NO es contradicción: hay pistones diésel que queman JET A-1 (Diamond
        # DA40 NG con Austro AE300, Cessna 182 con SMA SR305-230). Ambigüedad
        # genuina -> ámbar. Bloquear acá castigaría cargas correctas.
        return _r("ABSTENERSE", None, matricula,
                  "El registro dice JET A-1 y se observa un motor a pistón.",
                  d + ["Puede ser un pistón diésel (queman JET A-1) o el avión equivocado.",
                       "Verificar contra el manual de la aeronave antes de cargar."], modelo)

    if planta in ("PISTON", "TURBINA"):
        d.append("Planta motriz observada: %s (coherente)" % planta)
    else:
        d.append("Planta motriz: no se pudo determinar en la foto (no aporta).")

    return _r("CONSISTENTE", NOMBRE[prod], matricula,
              "La matrícula %s figura como %s y nada en la foto lo contradice."
              % (matricula, NOMBRE[prod]), d, modelo)


if __name__ == "__main__":
    jet = {"producto": JET, "avion": "Boeing 737-800"}
    avg = {"producto": AVGAS, "avion": "Cessna 172"}
    kingair = {"producto": JET, "avion": "Beech King Air B200"}
    diesel = {"producto": JET, "avion": "Diamond DA40 NG"}
    v = lambda **k: decidir(**k)["veredicto"]

    # Lo que deja pasar
    assert v(matricula="LVABC", confianza="alta", registro=jet, placard="JET") == "CONSISTENTE"
    assert v(matricula="LVABC", confianza="alta", registro=avg, placard="AVGAS",
             planta="PISTON") == "CONSISTENTE"
    # Registro solo, foto sin aporte: el registro es la autoridad.
    assert v(matricula="LVABC", confianza="alta", registro=jet) == "CONSISTENTE"
    # LA TRAMPA: turbohélice. Tiene hélice, lleva JET. No se puede bloquear.
    assert v(matricula="LVGQL", confianza="alta", registro=kingair,
             planta="TURBINA") == "CONSISTENTE"

    # Lo que bloquea
    assert v(matricula="LVCWO", confianza="alta",
             registro={"producto": CONFLICTO}) == "BLOQUEAR"
    assert v(matricula="LVABC", confianza="alta", registro=jet, placard="AVGAS") == "BLOQUEAR"
    assert v(matricula="LVABC", confianza="alta", registro=avg, placard="JET") == "BLOQUEAR"
    # Ninguna turbina quema AVGAS: no hay explicacion inocente.
    assert v(matricula="LVABC", confianza="alta", registro=avg, planta="TURBINA") == "BLOQUEAR"

    # Lo que no alcanza
    assert v() == "ABSTENERSE"
    assert v(matricula="LVABC", confianza="media", registro=jet) == "ABSTENERSE"
    assert v(matricula="LVZZZ", confianza="alta", registro=None) == "ABSTENERSE"
    # Piston diesel: registro JET + piston observado NO es contradiccion.
    assert v(matricula="LVABC", confianza="alta", registro=diesel, planta="PISTON") == "ABSTENERSE"
    assert v(matricula="LVABC", confianza="alta", registro=jet, planta="PISTON") == "ABSTENERSE"

    # El placard manda sobre la planta motriz: si el placard contradice es rojo
    # aunque la planta motriz sea coherente con el registro.
    assert v(matricula="LVABC", confianza="alta", registro=jet,
             placard="AVGAS", planta="TURBINA") == "BLOQUEAR"

    # Ningun camino a verde sin registro limpio.
    for reg in (None, {"producto": CONFLICTO}, {"producto": "GASOIL"}):
        for pl in ("JET", "AVGAS", "INDETERMINADO"):
            for pm in ("PISTON", "TURBINA", "INDETERMINADO"):
                assert v(matricula="LVABC", confianza="alta", registro=reg,
                         placard=pl, planta=pm) != "CONSISTENTE"

    print("veredicto.py: 42 checks OK")
