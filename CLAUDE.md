# Instrucciones para Claude en este repo

Sistema de turnos de abastecimiento de combustible aeronáutico.
FastAPI + SQLite sobre disco persistente de Render + Jinja2 + JS vanilla.

**Este repo es PÚBLICO.** No commitees matrículas de clientes fuera del maestro
que ya está versionado, ni credenciales, ni rutas absolutas de tu máquina, ni
URLs de sesión de agente.

---

## Lo primero, siempre: `git fetch`

`main` se mueve rápido y por PRs. **Dos veces** en este proyecto se construyó
contra un clon atrasado y se reimplementó código que ya existía — la segunda vez
fueron 13 commits de atraso y hubo que tirar un permiso, un módulo de registro y
todo el guardado de fotos.

```bash
git fetch origin
git log --oneline main..origin/main   # ¿cuánto me falta?
git diff --stat $(git merge-base HEAD origin/main) origin/main
```

Ramificá desde `origin/main`, no desde tu local. Rama: `feat/<algo>`.

**Antes de escribir un helper, buscá si ya existe.** Lo que más se duplicó acá:

| Necesidad | Ya existe en |
|---|---|
| Normalizar matrícula | `app/matricula.py` → `normalize_matricula` |
| Matrícula → combustible | `app/matricula.py` → `lookup_matricula` |
| Normalizar grado (JET A-1 / AVGAS 100LL) | `app/matricula.py` → `normalize_grado` |
| Comparar dos grados | `app/matricula.py` → `grados_compatibles` |
| Permisos | `app/auth.py` → `require_user`, `require_admin`, `require_operador`, `require_operador_or_coord`, `require_user_manager` |
| Guardar foto de toma | `app/toma_storage.py` → `save_toma_upload` + modelo `TomaFoto` |
| Normalizar input de matrícula en vivo | `static/js/matricula_input.js` (`data-matricula-input`) |
| Tokens firmados del QR | `app/toma_qr.py` |

## ESTADO.md se actualiza con cada cambio

En el **mismo commit** que el cambio. Si tocás algo que mueve el estado del
proyecto y no actualizás `ESTADO.md`, la entrega está incompleta. Es lo que lee
la próxima sesión —o la próxima persona— para saber dónde quedó todo.

## Roles

`CLIENTE` < `OPERADOR` < `NIVEL_1` < `NIVEL_2`. Ojo con esto:

- `user.is_admin` es **nivel 1 o 2**, y **excluye al operador de planta**.
- `require_operador` es **solo** el operador.
- `require_operador_or_coord` es operador **o** coordinación: es el "operario o más".

## Verificar antes de dar algo por bueno

```bash
python -m app.toma_veredicto   # 54 asserts del nucleo anti-misfueling
python -m app.toma_traba       # 11 asserts del estado de la traba
```

Van con `-m`: como script suelto no encuentran el paquete `app`.

Si un cambio necesita aflojar un assert de `toma_veredicto.py`, **no es un assert
de más: es el cambio que está mal.** Preguntá antes de tocarlo.

## `app/toma_veredicto.py` es el archivo delicado

Es el único donde un cambio razonable puede matar a alguien: decide si el
producto que se va a cargar es el que la aeronave lleva. Si esto falla hacia el
lado malo, se carga Jet A-1 en un avión a pistón y el motor se para en el
ascenso.

Sus invariantes están escritos en el docstring del archivo. Los dos que más se
proponen romper sin darse cuenta:

1. **La traba solo AGREGA una restricción.** Solenoide desenergizado = pico
   libre = como se trabaja hoy. Sin luz, sin wifi o sin server, el ESP libera por
   watchdog. Si alguien propone invertir el cableado (energizado = libre), un
   corte de luz traba toda la aeroplanta y el relé termina puenteado esa noche.
   Solo el rojo traba; el ámbar nunca.
2. **Los vetos son asimétricos a propósito.** Maestro AVGAS + turbina observada
   es rojo (ninguna turbina quema AVGAS); maestro JET + pistón observado es
   **ámbar**, porque existen pistones diésel que queman Jet A-1 (Diamond DA40 NG
   con Austro AE300, Cessna 182 con SMA SR305-230). El que proponga simetrizarlo
   tiene que explicar esos motores.

Y el que nunca se olvida: **un turbohélice tiene hélice y lleva JET.** King Air,
Caravan, PC-12, TBM, AT-802. Ver una hélice no significa pistón: ese es el error
que mata, y por eso la regla de la NTSB SA-051 es identificar la aeronave **por
matrícula, no por marca o modelo** — la apariencia miente cuando hay una
conversión STC.

Detalle completo en [`docs/VERIFICAR_TOMA.md`](docs/VERIFICAR_TOMA.md).

## Levantarlo en otra máquina

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
cp .env.example .env                             # completá lo que necesites
python -m alembic upgrade head
python -m scripts.import_maestro_matriculas --replace   # 2105 matriculas
uvicorn app.main:app --reload
```

Con `DEV_LOGIN=true` entrás sin Google. Para probar `/toma` hace falta rol de
operador o coordinación: `UPDATE users SET role='operador';` en la base local.

`turnos.db` está gitignoreado: cada quien tiene la suya.

## Convenciones

- **Sin frameworks de frontend.** Jinja2 + JS vanilla, sin CDN. La app se usa
  en rampa, con señal mala.
- Comentarios y mensajes de commit en español.
- Un comentario explica **por qué**, no qué. Si dice lo que el código ya dice,
  sobra.
- Los nombres de variables y funciones, en español donde el dominio es español
  (`matricula`, `grado`, `abastecedora`).
