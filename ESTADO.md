# Estado del proyecto

Última actualización: **2026-09-18**

Se actualiza **en el mismo commit** que el cambio. Ver [`CLAUDE.md`](CLAUDE.md).

---

## Qué hay andando

Sistema de turnos para aeroplantas YPF: el cliente reserva, coordinación arma la
agenda y asigna abastecedora y operador, y el operador de planta marca ABASTECIDO
o AUSENTE. Desplegado en Render, SQLite sobre disco persistente en `/var/data`,
una sola instancia y un solo worker (es la condición para que SQLite sea seguro).

**Maestro de matrículas:** 2105 aeronaves, 1699 JET A-1 y 406 AVGAS 100LL, sin
duplicados. Vive en la tabla `matriculas_combustible` y se carga con
`python -m scripts.import_maestro_matriculas --replace` desde
`data/maestro-aviones-version-final.xlsx`. Full replace en cada import.
Ver [`docs/MAESTRO_MATRICULAS.md`](docs/MAESTRO_MATRICULAS.md).

---

## En curso: verificación de toma + traba del pico

Rama **`feat/verificar-toma`**, commit `580b5b3`. **Pusheada, sin PR todavía.**

Pantalla `/toma` (menú ☰, para operador de planta o coordinación). El operador
elige el equipo con el que va a cargar, tipea la matrícula de la aeronave que
está viendo y saca una foto de su toma de combustible.

**Cierra un agujero real:** hoy `coord.py` compara el grado de la abastecedora
contra lo que el cliente **declaró** y contra la agenda, pero nunca contra el
maestro. Si el cliente declara AVGAS, la agenda es AVGAS y la abastecedora es
AVGAS, todo pasa — aunque el avión que está enfrente sea un turbohélice que lleva
JET. Nadie mira eso hoy.

Ahora se cruza **lo que la aeronave necesita** (maestro, por matrícula) contra
**lo que la manguera va a tirar** (`Abastecedora.grado`). Las dos puntas son dato
duro y la comparación es de strings: no depende de ningún modelo.

Ante contradicción se traba el pico de ese equipo, y un ESP32 lo aplica.
**Polaridad: solenoide desenergizado = pico libre = como se trabaja hoy.** El
sistema solo puede agregar una restricción, nunca sacarla.

Detalle completo en [`docs/VERIFICAR_TOMA.md`](docs/VERIFICAR_TOMA.md).

### Falta para que funcione en producción

1. **Abrir el PR** de `feat/verificar-toma` hacia `main`.
2. Cargar **`TOMA_ESP_TOKEN`** en Render. Sin token, `/toma/estado` responde 401
   y el ESP libera por watchdog: el sistema verifica pero no traba nada.
3. Cargar el campo **`codigo`** en las abastecedoras. Sin código no hay a qué
   mandarle la señal; la pantalla verifica igual y lo avisa en el detalle.
4. **Armar el hardware.** Firmware en `hardware/traba_toma/`: copiar
   `secrets.h.example` a `secrets.h` (gitignoreado) y completar wifi, host, token
   y el código de la abastecedora. El ESP se cuelga del hotspot de la tablet del
   operario.

---

## Fixes de seguridad 2026-09-18

Tres fixes puntuales, sin tocar `toma_veredicto.py`:

1. **Stored XSS en `important_info`.** Lo escribe un admin y se renderizaba con
   `| replace("\n", "<br>") | safe` sin escapar antes: HTML del campo quedaba
   marcado como seguro. Ahora es `| e | replace("\n", "<br>"|safe)`, sin `| safe`
   final, en `app/templates/agenda.html`,
   `app/templates/emails/confirmation.html` y `app/templates/emails/reminder.html`.
   El `|safe` va en el ARGUMENTO: `| e` devuelve un `Markup` y
   `Markup.replace()` escapa lo que le pasás, así que `| e | replace("\n", "<br>")` a secas escapa el `<br>` y rompe los saltos de línea.
2. **Timing side-channel en el token del ESP.** `app/routers/toma.py` comparaba
   el header `x-toma-token` con `!=`. Pasado a `hmac.compare_digest`,
   normalizando el header a `""` para no explotar con `None`.
3. **Bypass de `_safe_next` con backslash.** `app/routers/auth_routes.py`
   dejaba pasar `/\evil.com` (el navegador lo normaliza a `//evil.com`). Se
   rechaza también cuando el segundo carácter es `\`.

### Lo que NO está hecho, y por qué

**El clasificador de la toma no existe.** La foto se guarda como evidencia y
dataset, pero no se evalúa. No es un olvido: no hay ni una foto real de toma para
calibrarlo ni para validarlo, y un heurístico sin probar en un interlock de
combustible es peor que no tener ninguno — agrega confianza sin agregar
información.

El dataset se arma solo con el uso: cada verificación deja una fila en
`toma_fotos` con su matrícula, y la etiqueta se **deriva** uniendo contra el
maestro. Si algún día se corrige una matrícula, el dataset entero se re-etiqueta.

```sql
SELECT f.path, m.combustible
FROM toma_fotos f
JOIN matriculas_combustible m ON m.matricula = f.matricula;
```

Cuando haya unos cientos de fotos se entrena y se enciende el cruce en
`toma_veredicto.decidir(placard=...)`, que ya lo espera y ya tiene sus asserts.

**Va a clasificar forma, no tamaño.** La diferencia entre una toma AVGAS y una de
turbina es de 2,3" a 2,6" (FAA AC 20-122A): 7 mm sobre 60 mm, y mover el celular
10 cm cambia el tamaño aparente más que eso. La forma —chica con anillo
restrictor vs ancha y en "D"— no depende de la distancia.

**No hay dataset público que sirva.** Se buscó en Roboflow Universe y en la web:
lo que aparece son tapas de nafta de autos, boquillas de estación de servicio,
inspección industrial de boquillas, y reabastecimiento aire-aire (drogue/probe).
Ninguno es una toma de aeronave en tierra.

---

## Deuda conocida

- **El estado de la traba vive en memoria del proceso** (`app/toma_traba.py`).
  Vale porque hay una sola instancia y un worker, y porque perderlo es el lado
  seguro (se vuelve a "como hoy"). Con más de una instancia hay que pasarlo a
  tabla o Redis; el síntoma de no hacerlo sería una traba visible desde una
  instancia y no desde la otra.
- **La matrícula la tipea el operador mirando el avión.** Si tipea otra que
  existe y lleva el mismo combustible, el cruce no lo detecta. Se mitiga
  contrastando contra `Booking.aircraft`, pero no se elimina.
- **Nada prueba que la foto sea del avión cuya matrícula se tipeó.** Es el modo
  de falla de CEN15LA199, donde el personal confundió el avión con otro parecido
  en plataforma. Se mitiga con la confirmación humana.
- El firmware usa `setInsecure()`: no valida el certificado del server. El daño
  está acotado por la polaridad — lo peor que puede hacer un MITM es suprimir una
  traba, o sea dejar la operación como hoy.

---

## Cómo seguir desde otra máquina

Está en [`CLAUDE.md`](CLAUDE.md), sección *Levantarlo en otra máquina*. Lo único
que hay que saber de memoria: **`git fetch` antes de escribir una línea.** Dos
veces se construyó contra un clon atrasado y se reimplementó código existente.
