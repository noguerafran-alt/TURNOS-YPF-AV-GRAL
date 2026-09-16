# Maestro matrículas × combustible

Fuente: `data/maestro-matriculas-combustible.xlsx` (hoja `MATRICULAS Y COMBUSTIBLE`).

## Reglas

- Clave: `Matricula_Validada` (fallback `Matricula`), normalizada (trim, upper, sin espacios/guiones).
- `ProductoNombre` → grado interno: JET/AEROKEROSENE → `JET A-1`; AVGAS/100LL → `AVGAS 100LL`.
- Preferir filas con `Estado=OK` al resolver duplicados; el resto se importa si hay matrícula + grado válidos.
- Tabla: `matriculas_combustible` (`MatriculaCombustible`): matrícula única, combustible, modelo opcional, activo.
- **Sin candado de ownership.** Mis Aeronaves es lista personal aparte.
- Upsert de matrículas **nuevas** al maestro en operación: solo al marcar turno **ABASTECIDO** (panel coordinador). El import admin es la carga masiva inicial / refresh.

## Lookup / booking

- `GET /api/matricula/{matricula}` (usuario logueado): `found`, `primera_carga`, `unknown_matricula`, `combustible`, `modelo`.
- En el pedido de turno: si conocida → combustible readonly; si desconocida → aviso de primera carga. El servidor vuelve a resolver al crear el booking.

## Import

```bash
# default: data/maestro-matriculas-combustible.xlsx
python -m scripts.import_maestro_matriculas

# ruta / env
python -m scripts.import_maestro_matriculas --path /ruta/al.xlsx
MAESTRO_MATRICULAS_PATH=/ruta.xlsx python -m scripts.import_maestro_matriculas

# solo conteo
python -m scripts.import_maestro_matriculas --dry-run
```

También: botón **Reimportar maestro** en `/admin` (nivel 1+).

Dependencia: `openpyxl`.
