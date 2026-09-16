# Extracto live — flota y operadores

- **Fuente:** Turnos de Carga — Aeroplanta San Fernando (`turnosaeroplantas.com.ar`), Maestros.
- **Fecha de extracción:** 2026-09-16 (sesión ADMIN; datos visibles en vivo).
- **Sin mutaciones:** solo lectura; no se creó, editó, desactivó ni eliminó ningún registro.

## Maestros → Abastecedoras

**Total: 6**

| ID | Nombre | Grado | Capacidad (L) | Estado |
|---|---|---|---:|---|
| AB-01 | AB186QZ | JET A-1 | 9750 | Fuera de servicio |
| AB-02 | FOH265 | JET A-1 | 9800 | Activa |
| AB-03 | AC350AK | AVGAS 100LL | 5100 | Activa |
| AB-05 | JCP582 | JET A-1 | 4950 | Activa |
| SURT-AVG | Surtidor AVGAS | AVGAS 100LL | 999999 | Activa |
| SURT-JET | Surtidor JET A-1 | JET A-1 | 999999 | Activa |

CSV completo: `/workspace/turnos-ypf-design/abastecedoras.csv`

## Maestros → Operadores

**Total: 20 (todos Activo)**

| # | Nombre | Estado |
|---:|---|---|
| 1 | ALMARAZ, FRANCISCO | Activo |
| 2 | AÑASCO, MATIAS GABRIEL | Activo |
| 3 | CARO, SERGIO DANIEL | Activo |
| 4 | CASTILLO, MARIO | Activo |
| 5 | CUELLAR, ALEXIS | Activo |
| 6 | Carlos Medina | Activo |
| 7 | DALINGER, DANIEL | Activo |
| 8 | DOMINGUEZ, ALEJANDRO | Activo |
| 9 | Diego Suárez | Activo |
| 10 | GARCETE, FRANCISCO | Activo |
| 11 | LOPEZ, LEONARDO LUIS | Activo |
| 12 | LUNA, DANIEL | Activo |
| 13 | MUÑOZ, ANALIA ROCIO | Activo |
| 14 | NEGRETE, NICOLAS FEDERICO | Activo |
| 15 | PALAVECINO, JUAN MANUEL | Activo |
| 16 | PAREZ, ARIEL | Activo |
| 17 | Quiroga, Emiliano | Activo |
| 18 | Roberto Paz | Activo |
| 19 | VALDOVINO, GASTON | Activo |
| 20 | ZABALA, VICTOR | Activo |

CSV completo: `/workspace/turnos-ypf-design/operadores.csv`

## Maestros → Usuarios: roles presentes

- **admin:** 1
- **cliente:** 3
- **coordinador:** 1
- **Total usuarios:** 5 (todos activos al momento de consulta)
- **Confirmación:** no existe el rol de login **`operador`** en Usuarios. El selector de creación de usuario solo ofrece `coordinador`, `admin` y `cliente`.

### Nota operador-as-master

Los 20 operadores de Maestros son personas asignables a turnos en el maestro operativo; no constituyen cuentas de login. La aplicación indica que la operación de carga se gestiona en el otro sistema. Por tanto, **Operadores existe como maestro, pero `operador` no es un rol de usuario/login**.

## Estado de sesión

Se cerró sesión mediante **Salir** después de la extracción.
