# -*- coding: utf-8 -*-
"""Núcleo de decisión del interlock anti-misfueling. Sin I/O: entra evidencia, sale veredicto.

POLARIDAD DEL SISTEMA — lo único que hay que entender de este archivo:
el sistema solo puede AGREGAR restricciones, nunca sacarlas. En reposo el pico
está libre y el operador trabaja como siempre. La traba se activa ante una
contradicción; si el server se cae, se corta el wifi o este módulo falla, todo
queda exactamente como está hoy. Un error nuestro cuesta una parada al pedo,
nunca una carga equivocada.

EL CRUCE QUE IMPORTA: lo que la aeronave necesita (maestro, por matrícula)
contra lo que la manguera va a tirar (grado de la abastecedora). Hoy la app
compara el grado de la abastecedora contra lo que el cliente DECLARÓ y contra la
agenda (coord.py), pero nunca contra el maestro. Si el cliente declara AVGAS, la
agenda es AVGAS y la abastecedora es AVGAS, todo pasa — aunque el avión que está
enfrente sea un turbohélice que lleva JET. Ese es el agujero.

La matrícula manda sobre la apariencia: es la regla de la NTSB SA-051, porque un
avión con conversión STC se ve igual y lleva otro combustible.

Tres veredictos, y uno solo deja seguir:
  CONSISTENTE  la evidencia cruzada cierra. Confirma y habilita EL OPERADOR.
  ABSTENERSE   no alcanza la evidencia. Verificación manual. NO traba.
  BLOQUEAR     hay contradicción o dato sucio. No se carga, y TRABA el pico.
No existe el estado AUTORIZAR.

CIELO RASO CONOCIDO: la matrícula la tipea el operador mirando el avión. Si
tipea otra que existe y lleva el mismo combustible, el cruce no lo detecta. Se
mitiga contrastando contra la matrícula del turno, pero no se elimina.
"""

JET, AVGAS, CONFLICTO = "JET", "AVGAS", "CONFLICTO"
NOMBRE = {JET: "JET A-1", AVGAS: "AVGAS 100LL"}


def _r(veredicto, producto, matricula, motivo, detalle, modelo=None):
    # traba: solo el rojo traba. El ámbar es falta de evidencia, no contradicción,
    # y trabar por falta de evidencia llena la rampa de picos trabados al pedo.
    return {"veredicto": veredicto, "producto": producto, "matricula": matricula,
            "modelo": modelo, "motivo": motivo, "detalle": detalle,
            "traba": veredicto == "BLOQUEAR"}


def decidir(matricula=None, confianza="baja", registro=None,
            placard="INDETERMINADO", planta="INDETERMINADO",
            equipo=None, turno=None):
    """registro: la entrada del maestro para esa matrícula, o None si no figura.
    placard/planta: lo que se observó en la foto, o INDETERMINADO.
    equipo: grado de la abastecedora YA normalizado a 'JET' | 'AVGAS', o None.
            Lo normaliza el router; así este módulo no depende de SQLAlchemy.
    turno: {'aircraft': 'LVABC', 'declarado': 'AVGAS 100LL'} del booking, o None."""
    d = []

    if not matricula:
        return _r("ABSTENERSE", None, None, "No se pudo leer la matrícula.",
                  ["Escribí la matrícula de la aeronave que vas a cargar."])
    d.append("Matrícula: %s" % matricula)

    if confianza != "alta":
        return _r("ABSTENERSE", None, matricula,
                  "La matrícula se leyó con confianza %s." % confianza,
                  d + ["Verificar la matrícula a mano."])

    if registro is None:
        return _r("ABSTENERSE", None, matricula,
                  "La matrícula %s no figura en el maestro." % matricula,
                  d + ["Verificación documental antes de cargar."])

    prod = registro.get("producto")
    modelo = registro.get("avion") or registro.get("modelo")
    d.append("Maestro: %s%s" % (NOMBRE.get(prod, prod), " / %s" % modelo if modelo else ""))

    if prod == CONFLICTO:
        return _r("BLOQUEAR", None, matricula,
                  "La matrícula %s figura con AVGAS y con JET A-1." % matricula,
                  d + ["Dato en conflicto: no se resuelve en rampa.",
                       "Escalar a la aeroplanta antes de cargar."], modelo)

    if prod not in NOMBRE:
        return _r("BLOQUEAR", None, matricula,
                  "Producto desconocido en el maestro para %s." % matricula, d, modelo)

    # ---- EL CRUCE QUE MATA: lo que el avión necesita vs lo que la manguera tira.
    # Es determinístico, las dos puntas son dato duro, y es el único camino por el
    # que hoy se puede llegar a una traba sin depender de ningún modelo.
    if equipo in NOMBRE:
        d.append("Abastecedora: %s" % NOMBRE[equipo])
        if equipo != prod:
            return _r("BLOQUEAR", None, matricula,
                      "La aeronave lleva %s y la abastecedora es %s."
                      % (NOMBRE[prod], NOMBRE[equipo]),
                      d + ["NO CARGAR: el producto de la manguera no es el de la aeronave.",
                           "Cambiar de equipo o verificar la matrícula."], modelo)
    elif equipo is not None:
        d.append("Abastecedora: grado no reconocido (no aporta).")

    # ---- Contradicciones del turno: no hay peligro físico inmediato, pero algo
    # no cierra y conviene mirarlo antes de cargar. Ámbar, no rojo: el operador
    # puede estar cargando un sobreturno o un avión que el cliente cambió.
    if turno:
        otra = turno.get("aircraft")
        if otra and otra != matricula:
            d.append("El turno declara la matrícula %s." % otra)
            return _r("ABSTENERSE", None, matricula,
                      "La matrícula tipeada no coincide con la del turno (%s)." % otra,
                      d + ["Puede ser el avión equivocado, o un cambio de aeronave.",
                           "Confirmar cuál es el avión antes de cargar."], modelo)

        decl = turno.get("declarado")
        if decl and decl != NOMBRE[prod]:
            d.append("El turno declara %s." % decl)
            return _r("ABSTENERSE", None, matricula,
                      "El turno declara %s y el maestro dice %s." % (decl, NOMBRE[prod]),
                      d + ["El cliente declaró otro producto al reservar.",
                           "Verificar contra la placa de la aeronave."], modelo)

    # ---- La foto. Hoy siempre INDETERMINADO: el clasificador no existe todavía.
    # Los vetos son ASIMÉTRICOS a propósito: solo se bloquea donde no hay
    # explicación inocente, porque bloquear una carga legítima es como se le
    # enseña a un operador a puentear el solenoide.
    if placard in NOMBRE:
        d.append("Placard: %s" % NOMBRE[placard])
        if placard != prod:
            return _r("BLOQUEAR", None, matricula,
                      "El maestro dice %s pero el placard dice %s."
                      % (NOMBRE[prod], NOMBRE[placard]),
                      d + ["Contradicción entre maestro y aeronave. NO CARGAR."], modelo)

    if planta == "TURBINA" and prod == AVGAS:
        # No existe turbina que queme AVGAS. Contradicción sin salida inocente.
        return _r("BLOQUEAR", None, matricula,
                  "El maestro dice AVGAS 100LL pero se observa una turbina.",
                  d + ["Ninguna turbina quema AVGAS. NO CARGAR."], modelo)

    if planta == "PISTON" and prod == JET:
        # NO es contradicción: hay pistones diésel que queman JET A-1 (Diamond
        # DA40 NG con Austro AE300, Cessna 182 con SMA SR305-230).
        return _r("ABSTENERSE", None, matricula,
                  "El maestro dice JET A-1 y se observa un motor a pistón.",
                  d + ["Puede ser un pistón diésel (queman JET A-1).",
                       "Verificar contra el manual de la aeronave."], modelo)

    return _r("CONSISTENTE", NOMBRE[prod], matricula,
              "La aeronave %s lleva %s y nada lo contradice." % (matricula, NOMBRE[prod]),
              d, modelo)


if __name__ == "__main__":
    jet = {"producto": JET, "avion": "Beech King Air B200"}
    avg = {"producto": AVGAS, "avion": "Cessna 172"}
    diesel = {"producto": JET, "avion": "Diamond DA40 NG"}
    v = lambda **k: decidir(**k)["veredicto"]
    t = lambda **k: decidir(**k)["traba"]

    # Lo que deja pasar
    assert v(matricula="LVABC", confianza="alta", registro=jet, equipo=JET) == "CONSISTENTE"
    assert v(matricula="LVABC", confianza="alta", registro=avg, equipo=AVGAS) == "CONSISTENTE"
    assert v(matricula="LVABC", confianza="alta", registro=jet) == "CONSISTENTE"
    # Turbohelice: tiene helice y lleva JET. No se puede bloquear.
    assert v(matricula="LVGQL", confianza="alta", registro=jet, planta="TURBINA") == "CONSISTENTE"
    # El turno coincide: sin ruido.
    assert v(matricula="LVABC", confianza="alta", registro=jet, equipo=JET,
             turno={"aircraft": "LVABC", "declarado": "JET A-1"}) == "CONSISTENTE"

    # EL CRUCE QUE MATA: manguera equivocada -> rojo Y traba.
    assert v(matricula="LVABC", confianza="alta", registro=jet, equipo=AVGAS) == "BLOQUEAR"
    assert t(matricula="LVABC", confianza="alta", registro=jet, equipo=AVGAS) is True
    assert v(matricula="LVABC", confianza="alta", registro=avg, equipo=JET) == "BLOQUEAR"
    assert t(matricula="LVABC", confianza="alta", registro=avg, equipo=JET) is True
    # El caso real: cliente declara AVGAS, agenda AVGAS, abastecedora AVGAS,
    # pero el avion es un King Air que lleva JET. Hoy nadie lo mira.
    assert t(matricula="LVGQL", confianza="alta", registro=jet, equipo=AVGAS,
             turno={"declarado": "AVGAS 100LL"}) is True

    # Otros rojos: tambien traban.
    assert t(matricula="LVCWO", confianza="alta", registro={"producto": CONFLICTO}) is True
    assert t(matricula="LVABC", confianza="alta", registro=jet, placard=AVGAS) is True
    assert t(matricula="LVABC", confianza="alta", registro=avg, planta="TURBINA") is True

    # Ambar: NO traba nunca. Falta de evidencia no es contradiccion.
    for caso in (dict(),
                 dict(matricula="LVABC", confianza="media", registro=jet),
                 dict(matricula="LVZZZ", confianza="alta", registro=None),
                 dict(matricula="LVABC", confianza="alta", registro=diesel, planta="PISTON"),
                 dict(matricula="LVABC", confianza="alta", registro=jet,
                      turno={"aircraft": "LVXYZ"}),
                 dict(matricula="LVABC", confianza="alta", registro=jet,
                      turno={"declarado": "AVGAS 100LL"})):
        r = decidir(**caso)
        assert r["veredicto"] == "ABSTENERSE", (caso, r["veredicto"])
        assert r["traba"] is False

    # El equipo manda sobre el turno: si la manguera no coincide es rojo aunque
    # el turno tenga otra matricula (que por si sola seria ambar).
    assert v(matricula="LVABC", confianza="alta", registro=jet, equipo=AVGAS,
             turno={"aircraft": "LVXYZ"}) == "BLOQUEAR"

    # Ningun camino a verde sin maestro limpio.
    for reg in (None, {"producto": CONFLICTO}, {"producto": "GASOIL"}):
        for eq in (JET, AVGAS, None):
            assert v(matricula="LVABC", confianza="alta", registro=reg, equipo=eq) != "CONSISTENTE"

    print("toma_veredicto.py: 54 checks OK")
