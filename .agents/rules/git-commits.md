---
description: Convención obligatoria para la generación de mensajes de commit detallados (Conventional Commits con viñetas explicativas)
globs: "**/*"
---

# Regla de Mensajes de Commit: Formato Detallado y Estructurado

Siempre que el usuario solicite un mensaje de commit, o que se prepare un commit para el repositorio, se debe generar **obligatoriamente en formato detallado con viñetas**, siguiendo la convención **Conventional Commits**.

---

## 1. Estructura Obligatoria del Mensaje

El mensaje debe constar de:

1. **Línea de Título (Header)**:
   - Formato: `<tipo>(<alcance opcional>): <descripción concisa en imperativo/presente>`
   - Tipos válidos: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `chore`, `style`.
   - Longitud máxima recomendada: 72 caracteres.
   - Sin punto final.

2. **Línea en blanco obligatoria**.

3. **Cuerpo en Viñetas Detalladas (`- `)**:
   - Cada viñeta debe detallar un cambio técnico concreto, mencionando las funciones, clases, métodos o archivos involucrados usando backticks (ej. `SlurmInspector.get_job_accounting`, `shared/utils.py`).
   - Debe explicar claramente el *qué* y el *por qué* del cambio, no solo listar archivos modificados.
   - Si se agregaron o actualizaron pruebas, incluir el resultado de la suite de tests (ej. `(26/26 tests passing)`).

---

## 2. Plantilla y Ejemplo Canónico

```git
<tipo>: <resumen conciso de los cambios principales>

- Implementar `<Clase.metodo>` / `<modulo>` para resolver o añadir <funcionalidad>.
- Añadir utilidad `<funcion_o_helper>` en `<archivo>` para <proposito>.
- Actualizar lógica en `<componente>` para soportar <caso_de_uso>.
- Registrar nuevo comando CLI `<cluster-ai subcomando>` con sus argumentos y opciones.
- Incorporar suite de pruebas unitarias en `tests/<test_archivo>.py` (<N>/<N> tests passing).
```

### Ejemplo Real:

```git
feat: add Slurm accounting sync, auto-sync in compare, and improve CLI help

- Implement `SlurmInspector.get_job_accounting` to extract telemetry via `sacct` and `seff`.
- Add `parse_slurm_memory` utility in `shared/utils.py` for standard Slurm memory formats.
- Add `runner.sync_job` and transparent auto-sync in `compare` for non-cached job IDs.
- Register new CLI command `cluster-ai sync <job_id> [--mock]`.
- Enhance `cluster-ai --help` to show explicit command arguments/flags.
- Standardize database label to "Cluster AI DB" across outputs and documentation.
- Add comprehensive test suite in `tests/test_sync.py` (26/26 tests passing).
```

---

## 3. Entrega al Usuario

Siempre que se ofrezca un commit al usuario:
- Presentar el bloque de texto del commit listo para copiar.
- Adjuntar el comando de terminal correspondiente (`git add ... && git commit -m "..."`).
