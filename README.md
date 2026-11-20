# Cluster-AI (IALP HPC Assistant)

Asistente inteligente modular de diagnóstico, trazabilidad y optimización de simulaciones para el clúster de cómputo del **Instituto de Astrofísica de La Plata (IALP - CONICET / UNLP)**.

---

## Características Principales

1. **Trazabilidad Detallada de Recursos (Baseline vs Optimizado)**:
   - Registro automático en base de datos SQLite (`cluster.db`) de cada ejecución: tiempo de pared (walltime), tiempo de CPU, eficiencia de CPU (%), memoria pico residente (MaxRSS) y horas-núcleo consumidas.
   - Comparador empírico con cálculo de **Speedup**, ahorro de memoria y horas-núcleo salvadas (`cluster-ai compare <job1> <job2>`), o envío simultáneo de ambas versiones con espera y comparativa automática (`cluster-ai benchmark <script>`).

2. **Diagnóstico No Destructivo de Scripts Slurm (#SBATCH) y Corridas Cortas**:
   - **Original Intocable**: Nunca modifica, mueve ni borra el script original del investigador (por eso no genera copias `.bak`); produce una versión optimizada `<script>.optimized.sh`. Si al volver a diagnosticar ya existe una versión optimizada o un informe con otro contenido (por ejemplo, editado a mano), la versión anterior se conserva como `<nombre>_<YYYYMMDD_HHMMSS>.<ext>`.
   - **Validación Específica del Clúster IALP**: Ajuste automático de particiones (`short`, `medium`, `long`), límites de tiempo coherentes, control de CPUs por nodo según Slurm (32 en los 5950X, 24 en los 5900X) y aviso de uso de SMT, estandarización de logs con `%j` y recomendación de los módulos reales del servidor (`python/3.12`, `mpi/openmpi-x86_64`, `mpi/mpich-x86_64`, `intel/mpi/2021.14`).
   - **Corridas Cortas (`short-run` y `benchmark -d`)**: Permite ejecutar pruebas rápidas y acotadas en tiempo (ej. 5 a 10 minutos) de un script o del original y el optimizado a la vez, antes de enviar simulaciones que tardarían semanas o meses.

3. **Optimización Asistida por IA del Script #SBATCH (protocolo OpenAI)**:
   - Compatible con cualquier proveedor que implemente el protocolo OpenAI (`/chat/completions`): OpenAI oficial (`gpt-4o`, `gpt-4o-mini`), Agnes AI (`agnes-2.5-flash`), Groq, DeepSeek, Ollama local, etc.
   - **Fase actual:** el asistente revisa solo las directivas y recursos del script `#SBATCH` (cola, cores, memoria, tiempo, `srun`, afinidad). La auditoría del código fuente del proyecto descrita abajo **no está habilitada** en el servidor; en desarrollo local puede activarse con `CLUSTER_AI_ENABLE_CODE_AUDIT=true`.
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
- **Nodos de Cómputo (5 nodos / 68 cores físicos / 136 threads / 640 GB RAM):**
  - `node001`-`node002`: **AMD Ryzen 9 5950X** (16 núcleos / 32 hilos = 32 CPUs en Slurm, 128 GB RAM DDR4, SSD 500 GB, 2.5GbE).
  - `node003`-`node005`: **AMD Ryzen 9 5900X** (12 núcleos / 24 hilos = 24 CPUs en Slurm, 128 GB RAM DDR4, SSD 500 GB, 2.5GbE).
- **Colas / Particiones:**
  - `short`: Hasta 4 horas.
  - `medium`: Hasta 24 horas (**cola por defecto**).
  - `long`: Hasta 1 semana (168 horas).
  - `prolonged`: Más de 1 semana. **Restringida:** requiere autorización del administrador (no aparece en `sinfo` para usuarios comunes).

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
CLUSTER_AI_DB_PATH=cluster.db  # relativa a la raíz del proyecto, no al directorio actual
CLUSTER_AI_ENV=development    # 'production' oculta el emulador (comando mock y opciones --mock)
```

`CLUSTER_AI_ENV` define qué ve el investigador en `cluster-ai --help`. Si no está definida, el CLI se considera en **producción** cuando está instalado el helper del servidor (`/usr/local/libexec/cluster-ai-priv`) y en **desarrollo** en cualquier otro caso.

> En el servidor del clúster no se usa `.env`: la API Key y la base viven en un servicio privilegiado aislado. Ver [docs/configuracion_server.md](docs/configuracion_server.md).

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
# Diagnosticar y optimizar las directivas #SBATCH del script
uv run cluster-ai diagnose mi_simulacion.sh

# Diagnosticar en modo dry-run / mockup
uv run cluster-ai diagnose mi_simulacion.sh --mock
```
*Esto genera el script `mi_simulacion.optimized.sh` y el reporte `mi_simulacion_INFORME_MEJORA.md`; el original no se modifica. Al volver a diagnosticar, si alguno de esos archivos ya existía con otro contenido, la versión anterior se conserva como `mi_simulacion.optimized_<YYYYMMDD_HHMMSS>.sh` / `mi_simulacion_INFORME_MEJORA_<YYYYMMDD_HHMMSS>.md` (la fecha es la de cuando fue generada).*

### 3. Corridas Cortas de Prueba
```bash
# Ejecutar corrida corta de 10 minutos (por defecto) o personalizada
uv run cluster-ai short-run mi_simulacion.sh --duration 00:05:00
```

### 4. Benchmark: Original vs Optimizado en un solo paso
```bash
# Envía a la vez mi_simulacion.sh y mi_simulacion.optimized.sh (generado por diagnose),
# espera a que ambos terminen e imprime la comparativa:
uv run cluster-ai benchmark mi_simulacion.sh

# Corrida de prueba: ambos limitados a 10 minutos (en la cola más corta que lo admite):
uv run cluster-ai benchmark mi_simulacion.sh -d 00:10:00

# Indicar otro script optimizado, o solo enviar ambos sin esperar:
uv run cluster-ai benchmark mi_simulacion.sh -o otra_version.sh
uv run cluster-ai benchmark mi_simulacion.sh --no-wait
```
*La espera consulta `sacct` periódicamente. Con Ctrl+C (o `--no-wait`) los jobs siguen en la cola y se informa el `cluster-ai compare <base> <opt>` a ejecutar luego. Si alguno de los dos falla (`FAILED`, `CANCELLED`, `OUT_OF_MEMORY`, `NODE_FAIL`, ...), no se muestra la comparativa: se indica qué job falló y qué archivos de salida y error revisar (según sus `#SBATCH -o` / `-e`). `TIMEOUT` sí se compara: es lo esperado en una corrida de prueba y, sin `-d`, se avisa que el `#SBATCH -t` no alcanzó.*

*Si algún trabajo terminó por tiempo límite (`TIMEOUT`), tanto `benchmark` como `compare` muestran el tiempo, el speedup y las horas-núcleo como "no aplica (corrida acotada)": solo la eficiencia de CPU y la memoria pico son comparables.*

### 5. Comparativa de Recursos (Baseline vs Optimizado)
```bash
# Comparar jobs de producción (auto-sincroniza desde Slurm si el JobID no estaba en cluster.db):
uv run cluster-ai compare 1 2

# Comparar jobs de prueba:
uv run cluster-ai compare 1 2 --mock
```

### 6. Sincronización Directa desde Slurm (`sacct` / `seff`)
```bash
# Importar la telemetría de cualquier Job finalizado en Slurm hacia cluster.db:
uv run cluster-ai sync 1234

# Sincronizar en entorno de pruebas:
uv run cluster-ai sync 1 --mock
```

### 7. Historial de Trazabilidad
```bash
# Ver historial de producción:
uv run cluster-ai history

# Ver historial de pruebas:
uv run cluster-ai history --mock
```

### 8. Control del Emulador Slurm-Mockup
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
│   ├── filesystem.py           # Escrituras seguras (conservando versiones previas) y diffs
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
