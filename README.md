# Cluster-AI (IALP HPC Assistant)

Asistente inteligente modular de diagnóstico, trazabilidad y optimización de simulaciones para el clúster de cómputo del **Instituto de Astrofísica de La Plata (IALP - CONICET / UNLP)**.

---

## Características Principales

1. **Trazabilidad Detallada de Recursos (Baseline vs Optimizado)**:
   - Registro automático en base de datos SQLite (`cluster.db`) de cada ejecución: tiempo de pared (walltime), tiempo de CPU, eficiencia de CPU (%), memoria pico residente (MaxRSS) y horas-núcleo consumidas.
   - Comparador empírico con cálculo de **Speedup**, ahorro de memoria y horas-núcleo salvadas (`cluster-ai compare <job1> <job2>`).

2. **Diagnóstico No Destructivo de Scripts Slurm (#SBATCH) y Corridas Cortas**:
   - **Respaldo Inmutable Obligatorio**: Nunca modifica ni borra el script original del investigador; genera automáticamente una copia inmutable `<script>.sh.bak_<timestamp>` y produce una versión optimizada `<script>.optimized.sh`.
   - **Validación Específica del Clúster IALP**: Ajuste automático de particiones (`short`, `medium`, `long`, `prolonged`), límites de tiempo coherentes, control de sobreasignación de núcleos (16 cores en Ryzen 5950X, 12 en 5900X), estandarización de logs con `%j` y módulos (`module load openmpi`, `module load python3`).
   - **Corridas Cortas (`short-run`)**: Permite ejecutar pruebas rápidas y acotadas en tiempo (ej. 5 a 10 minutos) con telemetría en vivo mediante `psutil` antes de enviar simulaciones que tardarían semanas o meses.

3. **Auditoría Profunda de Código Fuente Científico (Asistente IA con protocolo OpenAI)**:
   - Compatible con cualquier proveedor que implemente el protocolo OpenAI (`/chat/completions`): OpenAI oficial (`gpt-4o`, `gpt-4o-mini`), Agnes AI (`agnes-2.5-flash`), Groq, DeepSeek, Ollama local, etc.
   - Inspección estática del código de simulación (Python, C, MPI, etc.) acoplado a la arquitectura Zen 3 de los procesadores AMD Ryzen.
   - Detección de cuellos de botella clásicos: contención por el GIL de Python, bucles nativos reemplazables por arrays vectorizados en NumPy / Numba, latencias de E/S a disco.
   - Generación automática de un informe técnico en Markdown (`<script>_INFORME_MEJORA.md`) con comparativas de código antes/después y justificación para HPC.

4. **Módulo Slurm-Mockup para Desarrollo y Dry-Runs Locales**:
   - Emulador completo de Slurm para probar flujos sin necesidad de estar conectado al clúster físico.
   - Emula comandos estándar: `sinfo`, `squeue`, `sbatch`, `sacct`, `seff`, `scancel`.

---

## Especificaciones del Clúster IALP (Extraídas del Manual)

- **Acceso:** `ssh usuario@163.10.86.211`
- **Nodo Cabecera:** Intel Celeron G5905 (2 núcleos @ 3.50 GHz, 32 GB RAM, SSD 500 GB + HDD 8 TB). *Prohibido ejecutar cómputo directo*.
- **Nodos de Cómputo (4 nodos / 56 cores físicos / 112 threads / 512 GB RAM):**
  - 2 nodos **AMD Ryzen 9 5950X** (16 núcleos / 32 hilos, 128 GB RAM DDR4, SSD 500 GB, 2.5GbE).
  - 2 nodos **AMD Ryzen 9 5900X** (12 núcleos / 24 hilos, 128 GB RAM DDR4, SSD 500 GB, 2.5GbE).
- **Colas / Particiones:**
  - `short`: Hasta 4 horas.
  - `medium`: Hasta 24 horas (**cola por defecto**).
  - `long`: Hasta 1 semana (168 horas).
  - `prolonged`: Trabajos de más de 1 semana.

---

## Instalación y Configuración

El proyecto utiliza [`uv`](https://docs.astral.sh/uv/) como gestor de paquetes de alto rendimiento:

```bash
# Clonar o ubicarse en el repositorio
cd cluster-ai

# Sincronizar dependencias
uv sync

# (Opcional) Instalar dependencias de desarrollo y tests
uv sync --extra dev
```

### Configuración de Variables de Entorno (`.env`)

Copiar la plantilla `.env.example` a `.env` y configurar las credenciales de tu proveedor preferido:

```env
# Proveedor de Inteligencia Artificial (Protocolo compatible con OpenAI)
OPENAI_API_KEY=tu_api_key_aqui
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini

CLUSTER_AI_MODE=cluster  # o 'mock' para pruebas locales
CLUSTER_AI_DB_PATH=cluster.db
```

---

## Guía de Uso del CLI

Cluster-AI expone los ejecutables duales `cluster-ai` y `cai`:

### 1. Ayuda y Versión
```bash
uv run cluster-ai --help
uv run cluster-ai --version
```
*Tanto la ayuda como la versión y todos los reportes muestran la firma del autor al final de la salida.*

### 2. Diagnóstico y Optimización de un Script
```bash
# Diagnosticar script y código fuente asociado
uv run cluster-ai diagnose mi_simulacion.sh

# Diagnosticar en modo dry-run / mockup
uv run cluster-ai diagnose mi_simulacion.sh --mock
```
*Esto genera una copia inmutable `mi_simulacion.sh.bak_<timestamp>`, el script `mi_simulacion.optimized.sh` y el reporte `mi_simulacion_INFORME_MEJORA.md`.*

### 3. Corridas Cortas de Prueba
```bash
# Ejecutar corrida corta de 10 minutos (por defecto) o personalizada
uv run cluster-ai short-run mi_simulacion.sh --duration 00:05:00
```

### 4. Comparativa de Recursos (Baseline vs Optimizado)
```bash
# Comparar jobs de producción:
uv run cluster-ai compare 1 2

# Comparar jobs de prueba:
uv run cluster-ai compare 1 2 --mock
```

### 5. Historial de Trazabilidad
```bash
# Ver historial de producción:
uv run cluster-ai history

# Ver historial de pruebas:
uv run cluster-ai history --mock
```

### 6. Control del Emulador Slurm-Mockup
```bash
# Ver estado de particiones y nodos del IALP
uv run cluster-ai mock sinfo

# Ver cola de trabajos
uv run cluster-ai mock squeue

# Enviar script simulado
uv run cluster-ai mock submit mi_simulacion.sh

# Consultar eficiencia de un trabajo
uv run cluster-ai mock seff 1
```

---

## Ejecución de Pruebas Automatizadas

```bash
uv run pytest tests/ -v
```

---

## Estructura del Proyecto

```text
cluster-ai/
├── config/
│   ├── cluster.yaml            # Especificaciones de hardware y colas del IALP
│   └── linters.yaml            # Reglas y umbrales determinísticos
├── knowledge/
│   └── rules/                  # Reglas XML (slurm, ialp_cluster, python_hpc, mpi)
├── shared/
│   ├── models.py               # Modelos Pydantic (JobRun, AnalysisResult, Benchmark)
│   ├── filesystem.py           # Respaldo inmutable (.bak), escrituras seguras y diffs
│   ├── utils.py                # Parseo de tiempos Slurm, hashing y autoría
│   └── exceptions.py           # Jerarquía de excepciones de dominio
├── modules/
│   ├── cli/                    # Entrypoints Typer (cluster-ai y cai)
│   ├── engine/                 # Orquestador (runner), contexto, storage SQLite y Agnes AI
│   ├── mock/                   # Emulador Slurm-Mockup para dry-run y pruebas locales
│   ├── inspectors/             # Inspección de Slurm y hardware
│   ├── linters/                # Linters de sbatch, python y mpi
│   └── reporters/              # Reportero de consola Rich e informes Markdown
├── tests/                      # Suite de tests automatizados (pytest)
├── cluster.db                  # Base de datos SQLite (excluida de git)
└── pyproject.toml              # Configuración de paquete y dependencias (uv / hatchling)
```

---

## Créditos y Autoría

**Autor:** Ing. Nehuen Pereyra  
**Equipo:** Soporte HPC - Instituto de Astrofísica de La Plata (IALP / CONICET / UNLP)  
**Contacto:** `ialp@fcaglp.unlp.edu.ar`
