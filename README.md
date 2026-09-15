# Sistema de turnos

App web para reservar turnos de abastecimiento de combustible aeronáutico.
FastAPI + SQLite sobre disco persistente + Jinja2 + JS vanilla.
Sin frameworks de frontend.

MVP de **una empresa con varias aeroplantas** (una aeroplanta = sede + producto).

> En el código el modelo se llama `Agenda` (es el concepto de agenda de turnos);
> en pantalla siempre se lee **Aeroplanta**.

---

## Cómo funciona

Los horarios disponibles **no se guardan en la base**. Se calculan al vuelo en
`app/slots.py`:

```
franjas semanales (ScheduleRule)
  − cortes puntuales (Closure)
  − turnos ya tomados (Booking, contra la capacidad de la agenda)
  − pasado / anticipación mínima / horizonte de reserva
  = grilla que ve el cliente
```

Así no hay que pre-generar filas de slots vacíos, y cambiar un horario de
atención se refleja al instante en toda la agenda.

Estados posibles de un horario: `free` (verde, clickeable), `taken` (gris),
`mine` (azul, tu turno), `closed` (rayado) y `past` (fuera de plazo).

### Estructura

```
TURNOS-APP/
├── app/
│   ├── main.py            arranque, middlewares, manejo de errores
│   ├── config.py          variables de entorno + validación de producción
│   ├── database.py        motor y sesiones de SQLAlchemy
│   ├── models.py          User, Agenda, ScheduleRule, Closure, Booking
│   ├── slots.py           generación de la grilla de horarios
│   ├── auth.py            Google OAuth + sesión + permisos
│   ├── templating.py      Jinja2 y filtros de fecha en español
│   ├── emails.py          emails transaccionales (Resend)
│   ├── migrate.py         aplicación de migraciones al arrancar
│   ├── reminders.py       barrida horaria de recordatorios
│   ├── routers/
│   │   ├── public.py      home, agenda, mis turnos
│   │   ├── bookings.py    API de reservas (JSON)
│   │   ├── admin.py       panel: aeroplantas, turnos, exportación
│   │   ├── users_admin.py gestión de usuarios (nivel 2)
│   │   ├── auth_routes.py login, logout, perfil
│   │   ├── coord.py       panel coordinador
│   │   └── external_ypf.py API READ-ONLY YPF (/external/v1)
│   ├── templates/         HTML
│   └── static/            CSS y JS
├── migrations/            historial de esquema (Alembic)
├── send_reminders.py      recordatorios a mano (la app los manda sola)
├── seed.py                datos de ejemplo
├── iniciar.bat            arranque local en un doble click
├── requirements.txt
├── render.yaml            deploy en Render
└── .env.example
```

### Rutas

| Ruta | Qué hace |
|---|---|
| `/` | Listado de aeroplantas activas |
| `/a/{slug}` | Calendario semanal de una aeroplanta |
| `/mis-turnos` | Turnos del cliente + cancelación |
| `/perfil` | Teléfono y empresa del cliente |
| `/auth/login` | Login con Google |
| `/admin` | Panel: estadísticas y aeroplantas |
| `/admin/usuarios` | Alta, niveles y bloqueo de usuarios (solo nivel 2) |
| `/admin/usuarios/{id}` | Ficha del cliente con todos sus datos y turnos |
| `/admin/agendas/{id}` | Horarios, cortes y reglas de una aeroplanta |
| `/admin/agendas/{id}/turnos` | Turnos reservados por semana |
| `/admin/turnos.csv` | Exportar turnos de todas las aeroplantas |
| `/admin/agendas/{id}/turnos.csv` | Exportar turnos de una aeroplanta |
| `POST /api/bookings` | Crear turno |
| `POST /api/bookings/{id}/cancel` | Cancelar turno |
| `/api/docs` | Documentación automática de la API |
| `/health` | Chequeo de salud |


### API externa YPF (READ-ONLY)

Prefijo `/external/v1`. Autenticación por `YPF_API_KEY` o `EXTERNAL_API_KEY`
(`X-API-Key` o `Authorization: Bearer`). Detalle de endpoints en
[`docs/YPF_API.md`](docs/YPF_API.md).


---

## Correr en local

**La forma fácil:** doble click en `iniciar.bat`. La primera vez crea el entorno
virtual, instala las dependencias, copia el `.env` y carga la base con datos de
ejemplo; después arranca el servidor y abre el navegador. Puerto opcional:
`iniciar.bat 8080`.

**A mano**, si preferís:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python seed.py
uvicorn app.main:app --reload
```

Abrir http://localhost:8000

Sin `DATABASE_URL`, usa SQLite en `./turnos.db`: no hace falta instalar nada.
Con `DEV_LOGIN=true` podés entrar con cualquier email sin configurar Google:
el botón aparece en `/auth/login`. Para verte como administrador, poné tu email
en `ADMIN_EMAILS`.

---

## Configurar el login con Google

1. Entrar a https://console.cloud.google.com/apis/credentials
2. Crear un proyecto y luego **Crear credenciales → ID de cliente de OAuth**
3. Tipo de aplicación: **Aplicación web**
4. Orígenes autorizados de JavaScript:
   - `http://localhost:8000`
   - `https://TU-SERVICIO.onrender.com`
5. URI de redireccionamiento autorizados:
   - `http://localhost:8000/auth/google/callback`
   - `https://TU-SERVICIO.onrender.com/auth/google/callback`
6. Copiar el **Client ID** y el **Client secret** al `.env`
   (`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`)

El `BASE_URL` tiene que coincidir con el dominio real: de ahí se arma la URL de
callback que se le manda a Google.

---

## Deploy en Render

La base es **SQLite sobre un disco persistente** de Render, no un Postgres
administrado. Para una empresa con unas pocas aeroplantas sobra: menos partes
móviles, costo fijo bajo y backups por snapshot del disco.

El `render.yaml` ya lo deja armado. Pasos:

1. Subir el repo a GitHub
2. En Render: **New → Blueprint** y elegir el repo
3. Completar las variables marcadas `sync: false` en el dashboard:
   `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ADMIN_EMAILS`, `RESEND_API_KEY`,
   `EMAIL_FROM`, `COMPANY_NAME`, `COMPANY_TAGLINE`, `SUPPORT_EMAIL`, `BASE_URL`
4. Agregar en Google Cloud la URI de redirección de producción:
   `https://TU-SERVICIO.onrender.com/auth/google/callback`
5. Entrar a `/admin` con un email de `ADMIN_EMAILS` y cargar las aeroplantas

`SECRET_KEY` la genera Render sola. En producción la app **se niega a arrancar**
si falta la clave, si `DEV_LOGIN` quedó prendido, si no hay credenciales de
Google, si `ADMIN_EMAILS` está vacío, o si los emails están activos sin API key.

### Qué implica el disco

El disco es lo que hace que los datos sobrevivan a cada deploy (el resto del
sistema de archivos de Render es efímero). A cambio impone tres condiciones:

* **Una sola instancia.** Un disco se monta en un solo servicio y en una sola
  instancia. Por eso el `render.yaml` fija `numInstances: 1` y el arranque usa un
  único worker: dos procesos escribiendo el mismo archivo SQLite se pisan.
* **Deploys con unos segundos de corte.** Render tiene que apagar la instancia
  vieja para liberar el disco antes de montar la nueva, así que no hay deploy sin
  interrupción. Para un sistema de turnos interno es irrelevante.
* **Sin Cron Jobs que toquen la base.** Un segundo servicio no vería el disco.
  Por eso los recordatorios los manda la propia app, en una tarea de fondo que
  barre cada hora (`app/reminders.py`).

Los discos requieren un plan pago (starter en adelante); en el plan free no
existen. Acordate de activar los **snapshots** del disco desde el dashboard: es
el backup de toda la base.

### Ajustes de SQLite para producción

En `app/database.py`, al abrir cada conexión:

| PRAGMA | Por qué |
|---|---|
| `journal_mode=WAL` | Lectores y escritor no se bloquean entre sí. Sin esto, un cliente reservando congela a los demás |
| `busy_timeout=30000` | Espera hasta 30 s si la base está ocupada, en vez de tirar "database is locked" |
| `synchronous=NORMAL` | Seguro ante caídas de la app; con `FULL` cada commit hace fsync y todo se vuelve lento |
| `foreign_keys=ON` | SQLite ignora las claves foráneas salvo que se pidan explícitamente |

### Si algún día hay que escalar

Crear una base Postgres, cambiar `DATABASE_URL` y sacar el bloque `disk` del
`render.yaml`. El código ya soporta las dos: las migraciones corren igual, y el
control de concurrencia pasa solo del lock en memoria a `SELECT ... FOR UPDATE`.

---

## Niveles de usuario

| Nivel | Puede |
|---|---|
| **Cliente** | Reservar y cancelar sus propios turnos |
| **Nivel 1 — Operador** | Todo lo anterior + panel: aeroplantas, horarios, cortes, ver y cancelar turnos de cualquiera, exportar CSV |
| **Nivel 2 — Administrador** | Todo lo del nivel 1 + dar de alta usuarios, cambiar niveles y bloquear cuentas |

Los niveles se manejan desde `/admin/usuarios`. Un nivel 2 puede **crear la cuenta
antes del primer ingreso**: cuando la persona entra con Google usando ese email,
cae en esa cuenta con el nivel ya asignado.

`ADMIN_EMAILS` es la llave de emergencia: esos emails recuperan el nivel 2 al
iniciar sesión, así nunca te quedás afuera del panel aunque la base esté recién
creada. Fuera de eso, los niveles **no** se pisan en cada login — si no, degradar
a alguien desde el panel no tendría efecto.

Reglas que impiden dejar el sistema sin administrador:

* nadie puede cambiarse el nivel a sí mismo ni bloquearse
* no se puede degradar ni bloquear al último nivel 2 activo

Bloquear una cuenta cierra su sesión en el próximo request.

---

## Emails

Tres mensajes automáticos, con plantillas en `app/templates/emails/`:

| Cuándo | Qué dice |
|---|---|
| Al reservar | Confirmación con horario, sede, matrícula e "Información importante" |
| Al cancelar | Aviso al cliente. Cambia el texto según si canceló él o la aeroplanta |
| 24 h antes | Recordatorio con botón para cancelar si no va a llegar |

Los envíos salen **en background**: si el proveedor está lento o caído, el cliente
igual recibe su confirmación en pantalla y la reserva queda hecha.

### Sin configurar nada (desarrollo)

Sin `RESEND_API_KEY`, los emails **no se envían**: se guardan como archivos `.html`
en `outbox/` y se loguean por consola. Abrilos en el navegador para ver exactamente
cómo se ven. Es lo que permite trabajar el contenido sin una cuenta de correo.

### En producción

1. Crear una cuenta en https://resend.com y verificar el dominio del remitente
2. Generar una API key en https://resend.com/api-keys
3. Cargar `RESEND_API_KEY` y `EMAIL_FROM` en Render

La app **se niega a arrancar en producción** si los emails están activos y falta la
API key: sin eso, se escribirían a disco y se darían por enviados en silencio.
Si no querés emails todavía, apagalos con `EMAIL_ENABLED=false`.

### Recordatorios

Los manda la propia app: una tarea de fondo barre cada hora los turnos que
arrancan dentro de `REMINDER_HOURS_BEFORE` y marca `reminder_sent_at`, así nunca
se duplican. No hay que configurar nada.

Para forzar un envío o probar en local:

```bash
python send_reminders.py --dry-run    # muestra a quién le mandaría, sin enviar
python send_reminders.py              # envía de verdad
```

La ventana de búsqueda es de 2 h con barridas cada hora: si el servicio se
reinicia justo en el medio de una, nadie se queda sin aviso.

---

## Migraciones (Alembic)

El esquema lo maneja Alembic, no `create_all`: un cambio de modelo se aplica sobre
la base existente **sin perder los datos**. La app corre `alembic upgrade head` al
arrancar, así después de cada deploy la base queda al día sola.

Cuando cambies un modelo:

```bash
alembic revision --autogenerate -m "descripcion del cambio"   # genera el archivo
# revisar y editar migrations/versions/<nuevo>.py
alembic upgrade head                                          # aplicar
alembic downgrade -1                                          # volver atrás
alembic check                                                 # ¿modelos y migraciones coinciden?
```

**Siempre revisá el archivo generado.** El encabezado de cada migración lista las
dos trampas más comunes:

* **Columna NOT NULL nueva sobre una tabla con datos**: necesita `server_default`,
  o falla en SQLite y en Postgres por igual.
* **Renombrar una columna**: autogenerate lo detecta como "borrar + crear", o sea
  que pierde los datos. Hay que cambiarlo a mano por `alter_column(new_column_name=...)`.

Si la migración mueve datos (no solo estructura), va con `op.execute(...)`. La
migración de niveles de usuario es el ejemplo: antes de borrar `is_admin`, hace
`UPDATE users SET role = 'nivel2' WHERE is_admin = 1`, y el downgrade lo revierte.

Con varias instancias en paralelo, apagá `RUN_MIGRATIONS` y corré
`alembic upgrade head` una sola vez antes del deploy (`preDeployCommand` en Render).

---

## Decisiones que conviene conocer

**Zona horaria.** Todo se guarda en UTC y se muestra en `TIMEZONE`
(por defecto Buenos Aires). El tipo `UTCDateTime` de `models.py` garantiza que
las fechas salgan de la base siempre con zona horaria: sin eso, SQLite las
devuelve "naive" y cualquier comparación revienta.

**Concurrencia.** Dos clientes pueden tocar el mismo horario en el mismo
segundo. La reserva se hace dentro de una transacción que primero bloquea la
fila de la agenda (`SELECT ... FOR UPDATE`), así los pedidos se serializan y el
cupo nunca se pasa. Como SQLite ignora `FOR UPDATE`, en desarrollo se usa un
lock en memoria (`app/routers/bookings.py`).

**Validación del horario.** El servidor no confía en el `starts_at` que manda
el navegador: recalcula la grilla y verifica que ese horario exista de verdad,
esté libre y respete la anticipación mínima.

**Permisos de admin.** Se definen por la variable `ADMIN_EMAILS`, no por un
campo editable desde la app: nadie puede auto-promoverse.

---

## Qué falta para producción

- **Multi-empresa**: hoy es una sola empresa; agregar una tabla `Company` y
  colgar las aeroplantas de ahí
- **PWA**: manifest + service worker para el "Instalar App"
- **Tests automatizados** en el repo (los de esta sesión fueron end-to-end contra
  el server real)
