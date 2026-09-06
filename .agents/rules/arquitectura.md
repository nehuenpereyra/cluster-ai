---
description: Reglas y directrices de arquitectura modular basada en pipeline para el proyecto Cluster-AI
globs: "**/*"
---

# Regla de Arquitectura: Pipeline y Módulos Extensibles (Cluster-AI)

Esta regla define los estándares arquitectónicos, la organización del código y las convenciones de diseño obligatorias para cualquier desarrollo o modificación dentro del repositorio **Cluster-AI**.

El proyecto sigue una **arquitectura basada en pipeline y módulos desacoplados**, optimizada para el clúster HPC del **Instituto de Astrofísica de La Plata (IALP - CONICET / UNLP)**.

---

## 1. Estructura de Directorios Canónica

Cualquier nuevo archivo debe respetar estrictamente la siguiente jerarquía:

```text
cluster-ai/
├── pyproject.toml              # Definición de dependencias, scripts CLI y empaquetado (uv / hatchling)
├── uv.lock                     # Lockfile determinístico
├── README.md                   # Documentación técnica de instalación y uso
├── .gitignore                  # Excluye cluster.db*, .env, *.bak*, __pycache__, etc.
├── cluster.db                  # Base de datos SQLite unificada (persistente, en la raíz, NO versionada)
├── config/                     # Configuraciones YAML externas del clúster y linters
│   ├── cluster.yaml            # Especificaciones de hardware, particiones y topología IALP
│   └── linters.yaml            # Umbrales y directivas de linters determinísticos
├── knowledge/                  # Base de conocimiento curada
│   ├── rules/                  # Reglas XML (slurm.xml, ialp_cluster.xml, python_hpc.xml, mpi_openmp.xml)
│   └── templates/              # Plantillas canónicas de scripts sbatch
├── shared/                     # Capa transversal compartida (evita dependencias circulares)
│   ├── __init__.py
│   ├── models.py               # Modelos de datos Pydantic (JobRun, LintIssue, AnalysisResult, etc.)
│   ├── utils.py                # Funciones puras (parseo de tiempos Slurm, hashing, autoría)
│   ├── exceptions.py           # Jerarquía de excepciones semánticas (ClusterAIError, etc.)
│   └── filesystem.py           # Lecturas seguras, respaldos inmutables (.bak) y diffs
└── modules/                    # Paquete funcional principal (REGLA: Todos sus hijos son carpetas)
    ├── __init__.py
    ├── cli/                    # Entrypoints de consola Typer (`cluster-ai` y `cai`)
    │   ├── __init__.py
    │   ├── main.py             # App Typer, callbacks globales y registro de subcomandos
    │   └── commands.py         # Implementación de subcomandos (diagnose, run, short-run, compare, history, mock)
    ├── engine/                 # Motor central de orquestación
    │   ├── __init__.py
    │   ├── runner.py           # Orquestador del ciclo de vida (Inspectors -> Linters -> IA -> Reporters)
    │   ├── context.py          # Ensamblador de contexto híbrido (Hardware + Reglas XML + Código Fuente)
    │   ├── storage.py          # Repositorio SQLite transaccional sobre cluster.db
    │   └── ai.py               # Cliente HTTP contra Agnes AI (`agnes-2.5-flash`) con fallback offline
    ├── inspectors/             # Telemetría y estado del clúster
    │   ├── __init__.py
    │   ├── base.py             # Interfaz abstracta BaseInspector
    │   ├── slurm_inspector.py  # Consulta a Slurm real con delegación automática en SlurmSimulator
    │   └── hardware_inspector.py # Detección de topología de CPU, memoria y almacenamiento
    ├── linters/                # Validación y corrección determinística de scripts
    │   ├── __init__.py
    │   ├── base.py             # Interfaz abstracta BaseLinter
    │   ├── sbatch_linter.py    # Validación de directivas Slurm (#SBATCH) según políticas del IALP
    │   ├── python_linter.py    # Detección de cuellos de botella en Python (bucles nativos, GIL, I/O)
    │   └── mpi_linter.py       # Validaciones de paralelismo MPI y OpenMP
    ├── reporters/              # Generación de salidas y entregables
    │   ├── __init__.py
    │   ├── base.py             # Interfaz abstracta BaseReporter
    │   ├── console.py          # Visualización en terminal con paneles y tablas Rich
    │   └── markdown.py         # Compilación de informes técnicos exhaustivos en Markdown
    └── mock/                   # Emulador local Slurm-Mockup para pruebas y dry-run
        ├── __init__.py
        └── simulator.py        # Emulación completa de sinfo, squeue, sbatch, sacct, seff, scancel
```

---

## 2. Principios y Restricciones Arquitectónicas

### 2.1. Regla de Organización en `modules/`
- **Obligatorio:** Cada componente dentro de `modules/` debe ser un subpaquete (un directorio que contenga su propio `__init__.py`).
- **Prohibido:** No colocar scripts o módulos sueltos directamente en la raíz de `modules/`.

### 2.2. Capa Transversal `shared/`
- Todo modelo de datos Pydantic, función auxiliar reutilizable (como el parseo de formato de tiempo `D-HH:MM:SS`), excepción o helper de filesystem debe ubicarse en `shared/`.
- Ningún archivo dentro de `shared/` debe importar módulos desde `modules/`. Esta restricción elimina por completo el riesgo de importaciones circulares.

### 2.3. Persistencia en `cluster.db` y Aislamiento en `cluster_mock.db`
- La base de datos SQLite persistente `cluster.db` reside en la **raíz del proyecto** tanto en desarrollo como en el servidor de producción (nodo cabecera).
- **Aislamiento en Modo Mock:** Cuando se ejecuta en modo `mock` (`CLUSTER_AI_MODE=mock` o bandera `--mock`), el sistema conmuta automáticamente a **`cluster_mock.db`** para no alterar ni mezclar datos de prueba con las telemetrías reales del clúster.
- Ambas bases de datos (`cluster.db*` y `cluster_mock.db*`) están estrictamente excluidas de Git en `.gitignore`.
- Ningún módulo interactúa directamente con SQL crudo salvo `modules/engine/storage.py`, el cual garantiza transacciones atómicas y modo WAL (`PRAGMA journal_mode=WAL;`).

### 2.4. Integridad de Archivos de Investigadores
- **Inmutabilidad y Cero Pérdida de Datos:** Nunca modificar ni sobreescribir el script `.sh` o `.sbatch` de un investigador sin haber creado previamente una copia de respaldo inmutable:
  `<script_original>.sh.bak_<YYYYMMDD_HHMMSS>`
- Las directivas corregidas deben guardarse en un archivo complementario: `<script_original>.optimized.sh`.

### 2.5. Autonomía y Modo Dry-Run (Slurm-Mockup)
- El sistema nunca debe asumir que los comandos de Slurm (`sinfo`, `squeue`, `sbatch`, etc.) están disponibles en el PATH del sistema.
- `SlurmInspector` debe detectar si Slurm está instalado; si no lo está o si se especifica la bandera `--mock`, debe conmutar automáticamente a `modules.mock.simulator.SlurmSimulator` para permitir desarrollo, depuración y pruebas locales transparentes en cualquier sistema operativo.

### 2.6. Integración con Agnes AI (`agnes-2.5-flash`)
- Modelo oficial configurado: `"agnes-2.5-flash"`.
- Endpoint: `https://apihub.agnes-ai.com/v1/chat/completions`.
- Credenciales cargadas vía `AGNES_API_KEY` desde variables de entorno o archivo `.env`.
- **Modo de Contingencia:** El cliente `AgnesAIClient` debe implementar un mecanismo de fallback que genere auditorías basadas en reglas heurísticas y XML si el endpoint remoto responde con error de cuota (401/429/5xx) o corte de conectividad, garantizando que el usuario siempre reciba un informe de optimización.

### 2.7. Identidad del Autor y Salida de Comandos
- Los comandos de consola `cluster-ai` y su alias `cai` deben incluir **al final (pie / footer)** de todo output lo siguiente:
  ```text
  Autor: Ing. Nehuen Pereyra (CPA IALP)
  ```
- Esta regla aplica obligatoriamente a:
  - `cluster-ai --help`
  - `cluster-ai --version`
  - Todos los subcomandos (`diagnose`, `run`, `short-run`, `compare`, `history`, `mock`)
  - El pie de los informes Markdown generados.

---

## 3. Guía de Extensibilidad (Cómo Agregar Nuevas Funcionalidades)

### Caso A: Agregar un Nuevo Modelo de Datos
1. Definir la clase heredando de `BaseModel` en `shared/models.py`.
2. Actualizar las tablas en `modules/engine/storage.py` si requiere persistencia en `cluster.db`.

### Caso B: Agregar un Nuevo Linter (ej. para GROMACS o Fortran)
1. Crear `modules/linters/<nombre>_linter.py` heredando de `BaseLinter`.
2. Implementar `can_handle()`, `lint()` y `fix()`.
3. Exportarlo en `modules/linters/__init__.py` y agregarlo a los linters activos en `modules/engine/runner.py`.

### Caso C: Agregar un Nuevo Subcomando CLI
1. Implementar la función del comando en `modules/cli/commands.py`, asegurando que imprima el pie de autoría al finalizar.
2. Registrar el comando en `modules/cli/main.py` mediante `app.command(name="<nombre>")(commands.<funcion>)`.

### Caso D: Incorporar Nuevas Reglas de Conocimiento
1. Crear o editar archivos XML dentro de `knowledge/rules/<software>.xml`.
2. El ensamblador `modules/engine/context.py` los cargará dinámicamente y los inyectará en el prompt enviado a Agnes AI.
