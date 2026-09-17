# Maestro matrículas × combustible

Fuente canónica: `data/maestro-aviones-version-final.xlsx` (hoja `BASE FINAL`).
Copia sincronizada: `data/maestro-matriculas-combustible.xlsx` (hoja `BASE`).

## Schema (2105 únicos)

| Columna         | Uso                                      |
|-----------------|------------------------------------------|
| CodigoProducto  | informativo                              |
| Combustible     | → `JET A-1` / `AVGAS 100LL`              |
| Matricula       | as-is + normalize (clave alfanum upper)  |
| Avion           | → `modelo` y `tipo` (Maestros columna TIPO) |

Conteos esperados: **2105** total · **1699** JET A-1 · **406** AVGAS 100LL · 0 dups · 0 `#REF!`.

## Reglas

- Clave: `Matricula` normalizada (trim, upper, sin espacios/guiones).
- `Combustible` (o legacy `ProductoNombre`) → grado interno: JET/AEROKEROSENE → `JET A-1`; AVGAS/100LL → `AVGAS 100LL`.
- Tabla: `matriculas_combustible` (`MatriculaCombustible`): matrícula única, combustible, modelo/tipo (desde Avion), activo.
- **FULL REPLACE** en cada import: se borran las filas previas del maestro y se cargan las del archivo. El xlsx es la fuente de verdad; upserts runtime (turno **ABASTECIDO**) no se preservan entre reimports.
- **Sin candado de ownership.** Mis Aeronaves es lista personal aparte.

## Lookup / booking

- `GET /api/matricula/{matricula}` (usuario logueado): `found`, `primera_carga`, `unknown_matricula`, `combustible`, `modelo`, `tipo`.
- En el pedido de turno: si conocida → combustible readonly; si desconocida → aviso de primera carga. El servidor vuelve a resolver al crear el booking.

## Import

```bash
# default: data/maestro-aviones-version-final.xlsx
python -m scripts.import_maestro_matriculas --replace

# ruta / env
python -m scripts.import_maestro_matriculas --path data/maestro-matriculas-combustible.xlsx --replace
MAESTRO_MATRICULAS_PATH=/ruta.xlsx python -m scripts.import_maestro_matriculas --replace

# solo conteo (no escribe)
python -m scripts.import_maestro_matriculas --dry-run
```

`--replace`: borra el maestro y carga el xlsx (fuente de verdad). Sin flag, upserta.
Idempotente con el mismo archivo + `--replace`: 2105 filas.

Dependencia: `openpyxl`.
