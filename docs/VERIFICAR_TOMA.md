# Verificar toma — AVGAS o JET antes de cargar

Pantalla `/toma`. El operador **tipea la matrícula** de la aeronave que va a
cargar, saca una **foto de la toma de combustible del avión** (no del equipo de
despacho), y la pantalla dice qué producto corresponde.

**El software no habilita el pico.** Devuelve un veredicto y deja constancia.
Habilitar lo hace una persona, mirando la placa de la aeronave.

Si esto falla hacia el lado malo se carga Jet A-1 en un avión a pistón y se mata
a alguien en el ascenso. Ese es el presupuesto de todo lo que sigue.

## Quién entra

`require_operador_or_coord`, el dependency que **ya existe** en `app/auth.py`:
operador de planta, nivel 1 o nivel 2. Hace falta ese y no `require_admin`,
porque `is_admin` deja afuera al operador de planta. Se llega por el menú ☰.

## Reusa lo que ya está

| Necesidad | Qué usa | Dónde vive |
|---|---|---|
| Permiso | `require_operador_or_coord` | `app/auth.py` |
| Producto de la aeronave | `lookup_matricula` | `app/matricula.py` |
| Normalización de matrícula y grado | `normalize_matricula`, `normalize_grado` | `app/matricula.py` |
| Guardado de la foto | `save_toma_upload` + tabla `TomaFoto` | `app/toma_storage.py` |
| Normalización en vivo del input | `data-matricula-input` | `static/js/matricula_input.js` |

Nada de copias propias: dos implementaciones de lo mismo se desincronizan, y la
que queda vieja falla en silencio.

## El maestro

El producto sale de `matriculas_combustible` vía `app.matricula.lookup_matricula`
— el mismo camino que usa la reserva de turno. **No hay copia del maestro ni
normalización propia.** Dos fuentes que se pueden desincronizar son peores que
una, y una normalización distinta a la del import haría que no se encuentre
NINGUNA matrícula, en silencio.

Ver [MAESTRO_MATRICULAS.md](MAESTRO_MATRICULAS.md) para el import.

## Cómo decide

```
matrícula tipeada ──> lookup_matricula ──> matriculas_combustible
                                                    │
                                                    v
                          toma_veredicto ──> CONSISTENTE / ABSTENERSE / BLOQUEAR
```

| Veredicto | Cuándo | Traba el pico |
|---|---|---|
| **CONSISTENTE** | la matrícula figura y nada la contradice | no |
| **ABSTENERSE** | no figura, primera carga, falta matrícula o foto, o el turno no coincide | **no** |
| **BLOQUEAR** | el producto de la manguera no es el de la aeronave, el maestro está en conflicto, o el grado no se reconoce | **sí** |

El ámbar **no traba**: falta de evidencia no es contradicción, y trabar por falta
de evidencia llena la rampa de picos trabados al pedo — que es exactamente como
se le enseña a un operador a puentear el solenoide.

## El cruce que cierra el agujero

Hoy `coord.py` compara el grado de la abastecedora contra lo que el cliente
**declaró** y contra la agenda. Nunca contra el maestro. Entonces si el cliente
declara AVGAS, la agenda es AVGAS y la abastecedora es AVGAS, todo pasa — aunque
el avión que está enfrente sea un turbohélice que lleva JET.

Esta pantalla compara **lo que la aeronave necesita** (maestro, por matrícula)
contra **lo que la manguera va a tirar** (`Abastecedora.grado`). Las dos puntas
son dato duro y la comparación es de strings: no depende de ningún modelo.

Fuentes que se cruzan, todas determinísticas:

| Fuente | De dónde |
|---|---|
| Producto de la aeronave | `matriculas_combustible`, por la matrícula tipeada |
| Producto de la manguera | `Abastecedora.grado` del equipo elegido |
| Matrícula del turno | `Booking.aircraft` (si se pasa `booking_id`) |
| Producto declarado | `Booking.combustible_declarado` |

## La traba y el ESP

**Polaridad: solenoide desenergizado = pico libre = como se trabaja hoy.** El
sistema solo puede AGREGAR una restricción, nunca sacarla. Sin luz, sin wifi,
sin server o con el firmware colgado, el pico queda libre y la operación queda
igual de segura que antes de que existiera el aparato. Un error nuestro cuesta
una parada al pedo, nunca una carga equivocada.

**Nunca inviertas esa lógica.** Si el solenoide se cablea al revés (energizado =
libre), un corte de luz traba todos los picos de la aeroplanta y alguien puentea
el relé esa misma noche.

**El ciclo.** La comparación es por evento: las tres fuentes no cambian solas, así
que recalcularlas cada 2 s daría siempre lo mismo. Lo que corre en loop es el
enforcement — el ESP consulta `GET /toma/estado?equipo=<codigo>` con el header
`X-Toma-Token` y actúa el solenoide. **Evento para decidir, loop para sostener.**

**Watchdog.** Si el ESP no logra consultar por 10 s, libera. Que el sistema se
caiga nunca puede dejar un pico trabado.

**Cómo se libera**, en orden de lo que pasa de verdad:
1. El operador corrige y vuelve a verificar. Una verificación sin contradicción
   sobre el mismo equipo pisa la traba. Es el camino normal.
2. `POST /toma/liberar` con equipo y **motivo obligatorio**, que queda en la bitácora.
3. Vencimiento a los 30 min, para que una traba olvidada no deje un equipo
   inutilizado toda la noche.
4. El botón físico del ESP, que no pasa por el server y siempre gana.

El estado vive en memoria del proceso (`app/toma_traba.py`). Vale porque
`render.yaml` fija `numInstances: 1` y un worker, y porque perderlo es el lado
seguro. Con más de una instancia hay que pasarlo a tabla o Redis.

**Firmware** en `hardware/traba_toma/`. Copiá `secrets.h.example` a `secrets.h`
(gitignoreado) y completá wifi, host, token y el `codigo` de la abastecedora. El
ESP se cuelga del hotspot de la tablet del operario.

No existe AUTORIZAR. Si el operador tipea mal, el maestro no la encuentra y cae
en ABSTENERSE, que es el lado seguro del error. El verde solo propone: abajo hay
un botón de confirmación que el operador toca después de mirar la placa, y **esa**
es la línea del rastro que dice que una persona decidió.

## La foto: hoy evidencia, mañana el cruce

**Hoy** la foto es la evidencia de la carga. No se evalúa automáticamente.

**Mañana** es el segundo factor. Cada foto va a la tabla `toma_fotos` con su
matrícula, igual que las que sube `/operador/api/toma-foto`. **La etiqueta no se
guarda: se deriva** uniendo `toma_fotos.matricula` contra `matriculas_combustible`.
Así hay una sola fuente de verdad del producto, y si el maestro se corrige, el
dataset entero se re-etiqueta solo.

```sql
SELECT f.path, m.combustible
FROM toma_fotos f
JOIN matriculas_combustible m ON m.matricula = f.matricula;
```

Cada verificación genera un ejemplo rotulado sin que nadie etiquete a mano.
Cuando haya unos cientos se entrena un clasificador y se enciende el cruce en
`toma_veredicto.decidir(placard=...)`, que ya lo espera y ya tiene asserts.

**Va a clasificar FORMA, no tamaño.** La toma AVGAS es chica y con anillo
restrictor visible; la de turbina es ancha y en "D". Eso es categórico y no
depende de la distancia. Medir no se puede: la diferencia es 2,3" contra 2,6"
(FAA AC 20-122A), o sea 7 mm sobre 60 mm, y mover el celular 10 cm cambia el
tamaño aparente más que eso.

### Cuando exista, los vetos van a ser asimétricos

Bloquear una carga legítima es cómo se le enseña a un operador a saltear la
herramienta. Solo se bloquea donde no hay explicación inocente:

- maestro **AVGAS** + turbina observada → **rojo**. Ninguna turbina quema AVGAS.
- maestro **JET** + pistón observado → **ámbar**. Hay pistones diésel que queman
  Jet A-1 (Diamond DA40 NG con Austro AE300, Cessna 182 con SMA SR305-230).

Y el que nunca se olvida: **un turbohélice tiene hélice y lleva JET**. King Air,
Caravan, PC-12, TBM, AT-802 todos llevan Jet A-1. Ese es el error que mata.
La regla de fondo es la NTSB SA-051: identificar la aeronave **por matrícula, no
por marca o modelo**, porque la apariencia miente cuando hay conversión STC.

## Archivos

| | |
|---|---|
| `app/toma_veredicto.py` | el núcleo de decisión. Función pura, sin I/O. **Leer primero.** |
| `app/routers/toma.py` | los endpoints, tras `require_operador_or_coord` (salvo `/toma/estado`, que usa token) |
| `app/templates/toma.html` | la pantalla. Cámara nativa, cero dependencias externas |
| `app/toma_traba.py` | estado de la traba por equipo, lo que consulta el ESP |
| `hardware/traba_toma/` | firmware del ESP32 que acciona el solenoide |

No hay migración nueva ni tabla nueva: se apoya en `toma_fotos`, que ya existe.

El campo de matrícula lleva `data-matricula-input`, así que lo normaliza en vivo
`static/js/matricula_input.js`, que ya usa el resto de la app.

## Verificar

```bash
python -m app.toma_veredicto     # 54 asserts del nucleo de decision
python -m app.toma_traba         # 11 asserts del estado de la traba
```

Va con `-m`: como script suelto no encuentra el paquete `app`. Si un cambio
necesita aflojar un assert, no es un assert de más: es el cambio que está mal.

La pantalla se revisa sin backend: entrá a `/toma` y desde la consola llamá a
`simular(...)` con `"CONSISTENTE"`, `"ABSTENERSE"`, `"BLOQUEAR"` o `"ERROR"`.

## Bitácora

Las fotos van por `save_toma_upload` a `TOMA_STORAGE_DIR` (default
`/var/data/tomas`) y quedan referenciadas en la tabla `toma_fotos`.

Aparte, en `TOMA_STORAGE_DIR/verificaciones/<fecha>/bitacora.jsonl` queda lo que
ninguna tabla cubre: veredicto, motivo, si trabó, y qué equipo. Sin la
foto la bitácora no se puede auditar después de un incidente. Si no se puede
escribir, la pantalla lo avisa en vez de callarse.

## Lo que NO hace

- **Hoy no evalúa la foto.** El veredicto sale del cruce determinístico; la
  foto es evidencia y material de entrenamiento.
- **No traba si no elegís equipo.** Sin abastecedora no hay con qué comparar la
  manguera ni a qué mandarle la señal.
- **No prueba que la foto sea del avión cuya matrícula se tipeó.** Se mitiga con
  la confirmación humana.
- **No mide el diámetro de la toma.**
- **No detecta combustible ya cargado ni mezclas.** Una mezcla de Jet A y AVGAS
  puede parecer AVGAS puro a simple vista (NTSB SA-050).
- **No distingue grados dentro de AVGAS**, ni Jet A de Jet A-1.
- **No dice nada de calidad de producto**: agua, sedimento, filtros, bonding.
  Eso es ATA 103 / JIG y ensayo físico.
