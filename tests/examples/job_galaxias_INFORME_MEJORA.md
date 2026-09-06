# Informe de Diagnóstico y Optimización de Simulación
**Proyecto / Script Evaluado:** `job_galaxias.sh`  
**Fecha:** 2026-09-06 12:34:19  
**Entorno de Ejecución:** Clúster HPC - Instituto de Astrofísica de La Plata (CONICET / UNLP)  
**Copia de Respaldo Inmutable:** `D:\Programacion\ialp\cluster-ai\tests\examples\job_galaxias.sh.bak_20260906_123419`  
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


## 4. Análisis Avanzado del Asistente (Agnes AI / Flash 2.5)
> [!WARNING]
> **Modo Contingencia Heurístico Activado**: Falta AGNES_API_KEY.
> El análisis a continuación ha sido generado mediante el motor de reglas estáticas y la base de conocimiento del clúster IALP.

### Auditoría y Recomendaciones Técnicas de Rendimiento (IALP HPC)

1. **Optimización de Vectorización y SIMD (Zen 3)**:
   - Los procesadores **AMD Ryzen 9 5950X y 5900X** del IALP cuentan con doble unidad de ejecución AVX2 por núcleo. Cualquier operación numérica en bucles iterativos puros de Python debe reemplazarse por arrays contiguos de NumPy o compilarse con `@numba.njit(fastmath=True)`.

2. **Gestión de Hilos y Arquitectura NUMA**:
   - El modelo Ryzen 5950X agrupa sus 16 núcleos en 2 Core Complex Dies (CCDs) de 8 núcleos cada uno con caché L3 compartida de 32MB. Para simulaciones multiproceso, fijar afinidad de procesos mediante `srun --cpu-bind=cores` para evitar saltos entre CCDs y penalizaciones de latencia de memoria.

3. **Evasión del Global Interpreter Lock (GIL)**:
   - Si la tarea es CPU-bound en Python, asegurar el uso de `concurrent.futures.ProcessPoolExecutor` o `mpi4py`. El uso de `threading.Thread` limitará la ejecución a un único hilo de cómputo efectivo.

4. **Estrategia de Almacenamiento SSD vs HDD**:
   - Utilizar el almacenamiento SSD local de 500GB en el nodo (`/scratch`) para archivos temporales, checkpoints intermedios y tablas frecuentes, reservando el disco HDD de 8TB para archivo definitivo de resultados.


## 5. Buenas Prácticas Operativas en el Clúster IALP
- **Topología de Nodos:** Recordar que los nodos de cómputo disponen de 16 núcleos (Ryzen 5950X) o 12 núcleos (Ryzen 5900X). Evitar solicitar más de 16 cores en un único nodo.
- **Almacenamiento Scratch:** Para lecturas y escrituras intensivas de simulación, utilizar los 500GB SSD locales de cada nodo (`/scratch`), reservando el disco magnético de 8TB para conjuntos de datos definitivos.
- **Corridas Cortas:** Utilice el comando `cluster-ai short-run <script.sh>` para validar el consumo de memoria y CPU antes de enviar trabajos extensos a las colas `long` o `prolonged`.

---

*Autor: Ing. Nehuen Pereyra (CPA IALP)*  

*Generado automáticamente por Cluster-AI - Soporte HPC IALP*