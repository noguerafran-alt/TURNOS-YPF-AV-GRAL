# API externa YPF — READ ONLY (fase 1)

Prefijo: **`/external/v1`**

Consumo de datos de turnos / coordinación por sistemas YPF. **Solo GET**.
No hay POST, PUT, PATCH ni DELETE bajo este prefijo.

## Autenticación

Configurar en el entorno **una** de:

- `YPF_API_KEY`
- `EXTERNAL_API_KEY`

(Se acepta cualquiera de las dos; la primera no vacía gana en el orden anterior.)

Enviar en cada request:

```http
X-API-Key: <clave>
```

o

```http
Authorization: Bearer <clave>
```

| Situación | Respuesta |
|---|---|
| Clave correcta | 200 (+ body) |
| Ausente / incorrecta | **401** |
| Ninguna variable de entorno seteada | **503** (misconfigured; no queda abierto) |

## Endpoints

### `GET /external/v1/health`

Chequeo autenticado del servicio externo.

```json
{ "ok": true, "service": "ypf-external", "version": "v1" }
```

### `GET /external/v1/agendas`

Lista aeroplantas/agendas.

Query: `active_only` (default `true`).

### `GET /external/v1/agendas/{id}`

Detalle: nombre, producto/grado, ubicación, reglas de slot resumidas
(`slot_rules_summary`: weekday + franja), capacidad, anticipación, etc.

### `GET /external/v1/agendas/{id}/horarios?date=YYYY-MM-DD`

Grilla del día (slots calculados al vuelo: `free` / `taken` / `closed` / `past`).
Si `date` falta, usa el día local actual (`TIMEZONE`).

### `GET /external/v1/abastecedoras`

Query opcionales: `agenda_id`, `grado`, `day=YYYY-MM-DD`.

Misma idea que el panel coordinador (compatibilidad de grado, fuera de servicio).

### `GET /external/v1/operadores`

Maestro `operadores` activos: `id`, `name`, `user_id` (nullable), `activo`.
Sin secretos ni tokens.

### `GET /external/v1/board`

Tablero del día — **mismo shape** que `/coord/board`.

Query: `agenda_id`, `date`, `status`, `q` (matrícula / combustible).

Respuesta: `kpis` + `bookings[]` con campos de coordinación
(`coordinacion_status`, `combustible_declarado`, `primera_carga`,
`unknown_matricula`, `sobreturno`, `origen`, asignación abastecedora/operador,
timestamps, etc.).

### `GET /external/v1/bookings/{id}`

Detalle de un turno.

### `GET /external/v1/matriculas/{matricula}`

Lookup stub (mismo shape que `/api/matricula/{matricula}`):
`found`, `primera_carga`, `unknown_matricula`, `combustible`.

## Notas

- No modifica multi-planta, OAuth ni CRUD de agendas.
- El health público de Render sigue en `/health` (sin API key).
- Documentación OpenAPI interactiva: `/api/docs` (incluye estos paths).

## Panel de administración

Los administradores **nivel 2** ven en `/admin` la sección **API de consulta (YPF)**
con estado de la clave (configurada/faltante, últimos 4 caracteres), base URL e
instrucciones. El secret se rota solo en Render Environment.
