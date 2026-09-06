# Informe de Diagnóstico y Optimización de Simulación
**Proyecto / Script Evaluado:** `job_galaxias.sh`  
**Fecha:** 2026-09-06 14:19:37  
**Entorno de Ejecución:** Clúster HPC - Instituto de Astrofísica de La Plata (CONICET / UNLP)  
**Copia de Respaldo Inmutable:** `D:\Programacion\ialp\cluster-ai\tests\examples\job_galaxias.sh.bak_20260906_141921`  
**Script Optimizado Sugerido:** `D:\Programacion\ialp\cluster-ai\tests\examples\job_galaxias.optimized.sh`  

---

## 1. Resumen Ejecutivo y Recomendaciones de Cola
- **Cola Recomendada:** `medium` (según el tiempo de ejecución estimado y límites de partición del IALP).
- **Integridad del Código Original:** El archivo original ha sido respaldado de manera inmutable antes de generar sugerencias.
- **Problemas Detectados en Directivas:** 6 observaciones.
- **Oportunidades de Mejora en Código:** 0 sugerencias algorítmicas.


## 2. Diagnóstico de Directivas Slurm (#SBATCH)
| Regla | Severidad | Diagnóstico | Sugerencia |
| :--- | :--- | :--- | :--- |
| `IALP-002` | **ERROR** | El tiempo solicitado (12:00:00) supera el límite de la cola 'short'. Su trabajo será cancelado antes de finalizar. | `Ajustar #SBATCH -t o cambiar a una cola superior (ej. 'long' o 'prolonged').` |
| `SLURM-001` | **WARNING** | El archivo de salida (-o) debe contener '%j' para evitar que se sobrescriban los logs de diferentes ejecuciones. | `#SBATCH -o salida-%j.out` |
| `SLURM-001` | **WARNING** | El archivo de error (-e) debe contener '%j' para trazabilidad de errores. | `#SBATCH -e salida-%j.err` |
| `IALP-003` | **ERROR** | Se solicitaron 24 núcleos en un solo nodo. Los nodos más grandes del IALP (Ryzen 9 5950X) poseen 16 núcleos físicos. | `Reducir cores a <= 16 por nodo o solicitar múltiples nodos (-N 2).` |
| `SLURM-003` | **WARNING** | El script invoca Python pero no contiene 'module load python3'. | `module load python3` |
| `PY-003` | **WARNING** | Operaciones de escritura a disco (I/O) dentro de un bucle de simulación. Esto degrada fuertemente el rendimiento por latencia de almacenamiento. | `Acumular en memoria en arrays NumPy o listas y volcar por lotes a /scratch o en formato binario HDF5/FITS.` |


### Comparativa de Directivas (Original vs Optimizado)
```diff
--- job_galaxias.sh (Original)+++ job_galaxias.sh (Optimizado)@@ -1,11 +1,14 @@ #!/bin/bash
 #SBATCH -J simulacion_galaxias
-#SBATCH -o salida.txt
-#SBATCH -e error.txt
+#SBATCH -o salida-%j.out
+#SBATCH -e salida-%j.err
 #SBATCH -t 12:00:00
 #SBATCH -N 1
-#SBATCH -n 24
-#SBATCH -p short
+#SBATCH -n 16
+#SBATCH -p medium
+
+# Carga de módulos según manual IALP
+module load python3
 
 # Ejecución del programa de astrofísica
 python3 simulacion_galaxias.py
```

## 3. Auditoría y Optimización del Código Fuente Científico
No se detectaron patrones ineficientes críticos en el análisis estático inicial del código fuente.


## 4. Análisis Avanzado del Asistente IA


# Informe de Auditoría HPC — Cluster GAMA (IALP)

## 1. Resumen de Diagnóstico de Recursos

| Parámetro | Valor Actual | Problema | Valor Recomendado |
|---|---|---|---|
| **Cola** | `short` (máx. 4h) | Tiempo solicitado 12h excede el límite → cancelación segura. | `medium` (máx. 24h) |
| **Cores** | `-n 24` | Supera el máximo de núcleos físicos por nodo (16 en 5950X, 12 en 5900X). Job quedará PENDING o será rechazado. | `-c 16` o `-n 16` |
| **Tiempo** | `12:00:00` | Incompatible con cola `short`. | `08:00:00` (en cola `medium`) |
| **Nodos** | `-N 1` | Correcto para este job. | Mantener `-N 1` |
| **Memoria** | No especificada | Sin límite definido, puede competir con otros jobs. | `--mem=32G` (32 GB por nodo de 128 GB) |
| **Trabajo** | Mono-nodo, serial | No explota paralelismo ni vectorización. | Optimizar código fuente. |

---

## 2. Mejoras en las Directivas #SBATCH

### Script Original vs. Optimizado

```bash
#!/bin/bash
#SBATCH -J simulacion_galaxias
#SBATCH -o salida-%j.out          # [SLURM-001] Trazabilidad única por JobID
#SBATCH -e error-%j.err           # [SLURM-001] Trazabilidad única por JobID
#SBATCH -t 08:00:00               # [IALP-002] Ajustado a cola medium (<=24h)
#SBATCH -N 1
#SBATCH -c 16                     # [IALP-003] Máximo núcleos disponibles en 5950X
#SBATCH -p medium                 # Cola adecuada para duración de 8h
#SBATCH --mem=32G                 # Límite de memoria explícito y conservador
#SBATCH --constraint=5950X        # Prioriza nodos con Ryzen 9 5950X (16 cores)

# Carga explícita de módulos del entorno
module load python3               # [SLURM-003] Evita inconsistencias de librerías

# Ejecución del programa de astrofísica
python3 simulacion_galaxias.py
```

**Cambios clave:**
- **Cola `short` → `medium`**: El tiempo de 12h excede el límite de 4h de `short`.
- **Cores 24 → 16**: Los nodos más potentes del clúster (comp01/comp02) tienen 16 núcleos físicos. Solicitar 24 causaría rechazo.
- **Output/Error con `%j`**: Evita sobrescritura de logs entre ejecuciones.
- **`module load python3`**: Garantiza consistencia del entorno.
- **`--mem=32G`**: Define un tope de memoria para no saturar el nodo.
- **`--constraint=5950X`**: Asegura ejecución en los nodos con 16 núcleos.

---

## 3. Auditoría Profunda del Código Fuente

### 3.1. Vectorización NumPy (Regla PY-001)

**Problema:** Bucle `for` nativo con `append` encadenado, ~50–200× más lento que operaciones vectorizadas.

```python
# ANTES (ineficiente)
resultados = []
for i in range(n_particulas):
    energia = calcular_energia(1.0, datos[i])
    resultados.append(energia)
```

```python
# DESPUÉS (vectorizado con NumPy)
import numpy as np

datos_arr = np.array(datos)
resultados = 0.5 * datos_arr ** 2   # Operación vectorizada sobre todo el arreglo
```

### 3.2. Uso del GIL — Multiprocessing (Regla PY-002)

**Problema:** Python no aprovecha múltiples núcleos debido al GIL. Aunque el cálculo actual es simple, una simulación de N-cuerpos real requeriría paralelismo.

```python
# ANTES (serial, 1 core)
from concurrent.futures import ProcessPoolExecutor
import numpy as np

def main():
    n_particulas = 5000
    datos = np.linspace(0, 499.9, n_particulas)

    # Distribuir el trabajo en chunkes para múltiples procesos
    chunk_size = n_particulas // 16
    chunks = [datos[i:i+chunk_size] for i in range(0, n_particulas, chunk_size)]

    with ProcessPoolExecutor(max_workers=16) as executor:
        resultados = list(executor.map(calcular_energia_batch, chunks))

    resultados = np.concatenate(resultados)
    # ...
```

```python
# Función auxiliar para multiprocessing (debe estar en nivel módulo)
def calcular_energia_batch(chunk):
    """Calcula energía cinética para un subconjunto de velocidades."""
    return 0.5 * chunk ** 2
```

### 3.3. I/O Intensivo — Almacenamiento Scratch (Regla PY-003)

**Problema:** Escritura en bucle a archivo en sistema de archivos compartido, latencia elevada.

```python
# ANTES (escritura lenta en bucle a disco compartido)
with open("resultados_temp.txt", "w") as f:
    for r in resultados[:100]:
        f.write(f"{r}\n")
```

```python
# DESPUÉS (escritura única, vectorizada, usando scratch local si hay I/O pesado)
import os

# Escribir todo el bloque de una vez (mucho más rápido)
scratch_path = "/scratch/$USER/resultados_temp.txt"
os.makedirs(os.path.dirname(scratch_path), exist_ok=True)

np.savetxt(scratch_path, resultados[:100], fmt="%.6f")
print(f"Datos guardados en: {scratch_path}")
```

### 3.4. Script Completo Optimizado

```python
"""
Simulación de N-cuerpos astronómicos simplificada para pruebas de HPC en IALP.
Optimizada con vectorización NumPy, multiprocessing y I/O eficiente.
"""

import os
import numpy as np
from concurrent.futures import ProcessPoolExecutor

def calcular_energia_batch(chunk):
    """Calcula energía cinética (0.5 * m * v^2) para un batch de velocidades."""
    masa = 1.0
    return 0.5 * masa * (chunk ** 2)

def main():
    n_particulas = 5000
    datos = np.linspace(0, 499.9, n_particulas)

    # Paralelismo basado en procesos (evasión del GIL)
    n_cores = int(os.environ.get("SLURM_CPUS_PER_TASK", 16))
    chunk_size = n_particulas // n_cores
    chunks = [datos[i:i+chunk_size] for i in range(0, n_particulas, chunk_size)]

    with ProcessPoolExecutor(max_workers=n_cores) as executor:
        resultados = list(executor.map(calcular_energia_batch, chunks))

    resultados = np.concatenate(resultados)

    # Escritura eficiente en bloque a scratch local
    scratch_path = f"/scratch/$USER/resultados_temp_{os.getpid()}.txt"
    os.makedirs(os.path.dirname(scratch_path), exist_ok=True)
    np.savetxt(scratch_path, resultados[:100], fmt="%.6f")

    print(f"Simulación finalizada. Partículas: {n_particulas}, "
          f"cores: {n_cores}, salida: {scratch_path}")

if __name__ == "__main__":
    main()
```

---

## 4. Impacto Estimado

| Métrica | Antes | Después | Mejora |
|---|---|---|---|
| **Velocidad de cálculo** | Serial, bucles Python | NumPy vectorizado + multiprocessing (16 procesos) | **~80–150×** (solo vectorización: ~100×; con multiprocessing adicional) |
| **Tiempo de ejecución estimado** | ~12s (bucles + I/O) | **<0


## 5. Buenas Prácticas Operativas en el Clúster IALP
- **Topología de Nodos:** Recordar que los nodos de cómputo disponen de 16 núcleos (Ryzen 5950X) o 12 núcleos (Ryzen 5900X). Evitar solicitar más de 16 cores en un único nodo.
- **Almacenamiento Scratch:** Para lecturas y escrituras intensivas de simulación, utilizar los 500GB SSD locales de cada nodo (`/scratch`), reservando el disco magnético de 8TB para conjuntos de datos definitivos.
- **Corridas Cortas:** Utilice el comando `cluster-ai short-run <script.sh>` para validar el consumo de memoria y CPU antes de enviar trabajos extensos a las colas `long` o `prolonged`.

---

*Autor: Ing. Nehuen Pereyra (CPA IALP)*  

*Generado automáticamente por Cluster-AI - Soporte HPC IALP*