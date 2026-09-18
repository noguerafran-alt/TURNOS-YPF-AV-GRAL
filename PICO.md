# Verificación de pico — AVGAS o JET antes de cargar

Pantalla `/pico` dentro de la app de turnos. El operador **tipea la matrícula** de
la aeronave que va a cargar, saca una **foto de la toma de combustible del avión**,
y la pantalla dice qué producto corresponde.

**El software no habilita el pico.** Devuelve un veredicto y deja constancia.
Habilitar lo hace una persona, mirando la placa de la aeronave.

Si esto falla hacia el lado malo se carga Jet A-1 en un avión a pistón y se mata
a alguien en el ascenso. Ese es el presupuesto de todo lo que sigue.

## No usa ningún servicio externo

Ni API key, ni modelo remoto, ni internet. La matrícula la tipea el operador
—cinco caracteres, más confiable que cualquier OCR— y el registro decide.
Determinístico y gratis.

## Quién entra

Nivel 1 (Operador) o nivel 2 (Administrador) — el dependency `require_admin`, el
mismo permiso que el panel. No hizo falta un rol nuevo: `User.is_admin` ya
devuelve `True` para los dos niveles, así que "operario o más" ya estaba escrito.

## Cómo decide

```
matrícula tipeada ──> normalizar() ──> aeronaves.json ──> pico_veredicto
                                       (2.105 matrículas)       │
                                                                v
                                     CONSISTENTE / ABSTENERSE / BLOQUEAR
```

| Veredicto | Cuándo | Qué pasa |
|---|---|---|
| **CONSISTENTE** | la matrícula figura con un producto limpio | el operador confirma contra la placa y habilita él |
| **ABSTENERSE** | la matrícula no figura, está vacía, o falta la foto | verificación manual |
| **BLOQUEAR** | la matrícula figura con AVGAS y JET a la vez, o no hay registro | no se carga |

No existe AUTORIZAR. Toda falla —registro ausente, JSON ilegible, red caída—
termina fuera del verde. Si el operador tipea mal, el registro no la encuentra y
cae en ABSTENERSE, que es el lado seguro del error.

## La foto: hoy evidencia, mañana el cruce

**Hoy** la foto de la toma es la evidencia de la carga y nada más: no se evalúa
automáticamente.

**Mañana** es el segundo factor. Cada foto se guarda ETIQUETADA con el producto
que dictó el registro:

```
2026-09-17T180411-a3f9_toma_JET-A-1.jpg
2026-09-17T180411-b7c2_toma_AVGAS-100LL.jpg
```

O sea que cada verificación genera un ejemplo rotulado sin que nadie etiquete a
mano. Cuando haya unos cientos se entrena un clasificador y se enciende el cruce
en `pico_veredicto.decidir(placard=...)`, que ya lo espera y ya tiene sus asserts.
Los veredictos sin producto quedan como `SIN-ETIQUETA` y no sirven para entrenar,
que es lo correcto.

**Va a clasificar FORMA, no tamaño.** La toma AVGAS es chica y con anillo
restrictor visible; la de turbina es ancha y en "D". Eso es categórico y no
depende de la distancia. Medir no se puede: la diferencia es 2,3" contra 2,6"
(FAA AC 20-122A), o sea 7 mm sobre 60 mm, y mover el celular 10 cm cambia el
tamaño aparente más que eso.

### Y cuando exista, los vetos van a ser asimétricos

Bloquear una carga legítima es cómo se le enseña a un operador a saltear la
herramienta. Solo se bloquea donde no hay explicación inocente:

- registro **AVGAS** + turbina observada → **rojo**. Ninguna turbina quema AVGAS.
- registro **JET** + pistón observado → **ámbar**. Hay pistones diésel que queman
  Jet A-1 (Diamond DA40 NG con Austro AE300, Cessna 182 con SMA SR305-230).

Y el que nunca se olvida: **un turbohélice tiene hélice y lleva JET**. King Air,
Caravan, PC-12, TBM, AT-802 todos llevan Jet A-1. Ese es el error que mata.

## El registro NO va en el repo

Son matrículas de clientes de YPF y **este repo es público**. `aeronaves.json`
vive en el disco persistente de Render, al lado de `turnos.db`:

```
/var/data/aeronaves.json          # PICO_REGISTRO apunta acá
```

Se regenera cuando cambia el maestro y se sube a mano al disco:

```bash
python construir_aeronaves.py maestro-aviones-version-final.xlsx aeronaves.json
```

En local, generalo en cualquier carpeta **fuera del repo** y exportá
`PICO_REGISTRO` con esa ruta. `.gitignore` ya bloquea `aeronaves.json`,
`*maestro*.xlsx` y `pico_registros/`.

Hoy: 2.105 matrículas, 1.699 JET y 406 AVGAS, sin conflictos. El camino de
`CONFLICTO` sigue en el código igual: si una regeneración futura trae la misma
matrícula con los dos combustibles, se bloquea y **no se desempata por ningún
criterio**. Un conflicto es dato sucio o un misfueling histórico, y ninguna de
las dos cosas se resuelve en rampa.

## Archivos

| | |
|---|---|
| `app/pico_veredicto.py` | el núcleo de decisión. Función pura, sin I/O. **Leer primero.** |
| `app/pico_registro.py` | carga el registro y resuelve matrícula → producto |
| `app/routers/pico.py` | los tres endpoints, todos tras `require_admin` |
| `app/templates/pico.html` | la pantalla. Cámara nativa, cero dependencias externas |
| `construir_aeronaves.py` | maestro `.xlsx` → `aeronaves.json` (se corre a mano) |

**`normalizar()` vive en `app/pico_registro.py`** y la importa
`construir_aeronaves.py` para escribir las claves. Si alguien la reimplementa en
otro lado, el sistema deja de encontrar matrículas y todo cae en ABSTENERSE **en
silencio**. Es el peor modo de falla del repo porque no rompe nada visible: solo
vuelve inútil la herramienta.

## Verificar

Desde la raíz del repo, con el intérprete del `.venv`:

```bash
python -m app.pico_veredicto                      # 42 asserts del nucleo
PICO_REGISTRO=... python -m app.pico_registro     # normalizacion contra el registro real
```

Van con `-m`: corridos como script suelto no encuentran el paquete `app`.

Si un cambio necesita aflojar un assert, no es un assert de más: es el cambio que
está mal.

La pantalla se revisa sin backend: entrá a `/pico` con sesión de nivel 1 o 2 y
desde la consola llamá a `simular(...)` con `"CONSISTENTE"`, `"ABSTENERSE"`,
`"BLOQUEAR"` o `"ERROR"`.

## Bitácora

Cada verificación escribe en `/var/data/pico_registros/<fecha>/bitacora.jsonl`
una línea con veredicto, matrícula tipeada, producto, motivo, el mail del
operador y el sha256 de la foto, y guarda la foto al lado. Sin la foto la
bitácora no se puede auditar después de un incidente. La confirmación del
operador se anota como una línea aparte (`CONFIRMADO_POR_OPERADOR`): esa es la
que dice que una persona decidió. Si no se puede escribir, la pantalla lo avisa
en vez de callarse.

## Lo que este sistema NO hace

- **Hoy no evalúa la foto.** El veredicto sale solo del registro. La foto es
  evidencia y material de entrenamiento.
- **No prueba que la foto sea del avión cuya matrícula se tipeó.** Se mitiga con
  la confirmación humana.
- **No mide el diámetro de la toma.** 2,3" contra 2,6" no es medible en una foto
  sin escala calibrada.
- **No detecta combustible ya cargado ni mezclas.** Una mezcla de Jet A y AVGAS
  puede parecer AVGAS puro a simple vista (NTSB SA-050).
- **No distingue grados dentro de AVGAS** (100LL vs UL91 vs 100), ni Jet A de Jet A-1.
- **No dice nada de calidad de producto**: agua, sedimento, densidad, filtros,
  bonding. Eso es ATA 103 / JIG y ensayo físico.
