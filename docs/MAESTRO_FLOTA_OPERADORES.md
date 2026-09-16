# Maestro flota: hangares + abastecedoras + operadores

Fuente live Turnera San Fernando (extracción 2026-09-16):

- `data/hangares.csv` — 17 filas (`Codigo,Nombre,Estado`)
- `data/abastecedoras.csv` — 6 filas (`ID,Nombre,Grado,Capacidad_L,Estado`)
- `data/operadores.csv` — 20 filas (`Nombre,Estado`)
- Contexto: `data/flota-operadores-extract.md`

## Modelo

### Hangares

Campos: `codigo` (H1…, PP, PN, PLAT_YPF), `nombre`, `agenda_id` nullable,
`capacidad` opcional, `activo`.

- Estado CSV **Activo** → `activo=true`
- **`agenda_id=NULL`**: maestro **global**. Visible en Maestros con Planta =
  «Todas / global» y también al filtrar una planta concreta (los listados
  incluyen `agenda_id IS NULL` OR la planta elegida).

### Abastecedoras

Campos: `codigo` (AB-01…), `nombre`, `grado`, `capacidad_l` (opcional), `activo`,
`fuera_de_servicio_hasta`, `agenda_id`.

- Estado CSV **Activa** → `activo=true`
- **Fuera de servicio** → `activo=false` (no aparece en el selector de asignación)
- **`agenda_id` se deja en `NULL`**: la unidad sirve en cualquier agenda del mismo
  grado; el gate de grado en `/coord/.../asignar` ya filtra JET vs AVGAS.
  (Alternativa descartada: amarrar a la Agenda cuyo `product` coincide — rompe
  multi-planta del mismo grado.)

### Operadores (maestro)

Tabla `operadores`: `nombre`, `nombre_norm` (único), `activo`, `user_id` nullable.

No son cuentas de login. El rol de login `operador` (PR#5) es aparte: cuando exista
un `User` con `role=operador` cuyo nombre/email normalizado coincida, el seed
puede enlazar `user_id`.

`Booking.operador_id` → FK al maestro. `operador_user_id` se espeja desde
`Operador.user_id` al asignar (compat panel `/operador` y datos legacy).

## Seed idempotente

```bash
python -m scripts.seed_flota_operadores
python -m scripts.seed_flota_operadores --dry-run
```

Upsert: hangares por `codigo`; abastecedoras por `codigo`; operadores por
`nombre_norm`. Re-ejecutar no duplica filas. Si falta `data/hangares.csv`, el
script omite hangares y sigue con abast/ops (o pasar `--hangares PATH`).

Tras migrar (`alembic upgrade head` / arranque de la app), correr el seed una vez
en cada entorno.
