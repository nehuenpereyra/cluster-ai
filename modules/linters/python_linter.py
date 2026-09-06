"""modules/linters/python_linter.py - Validador y detector de mejoras en scripts Python de simulación."""

import ast
import re
from typing import List, Tuple
from modules.linters.base import BaseLinter
from shared.models import CodeOptimizationSuggestion, LintIssue, LintSeverity

class PythonLinter(BaseLinter):
    """
    Analiza scripts Python de investigadores buscando ineficiencias computacionales
    típicas de simulaciones de astrofísica y ciencia de datos.
    """

    def can_handle(self, file_path: str, content: str) -> bool:
        return file_path.endswith(".py") or "python" in content.lower()

    def lint(self, file_path: str, content: str) -> List[LintIssue]:
        issues: List[LintIssue] = []

        # Analizar bucles anidados en Python nativo
        if re.search(r"for\s+\w+\s+in\s+range\(.+?\):\s*\n\s+for\s+\w+\s+in\s+range\(", content):
            issues.append(LintIssue(
                rule_id="PY-001",
                severity=LintSeverity.WARNING,
                message="Se detectaron bucles 'for' anidados intensivos. En HPC esto suele ser 50x-100x más lento que operaciones vectorizadas en NumPy o @numba.njit.",
                suggestion="Reemplazar por operaciones matriciales vectorizadas o compilar con Numba JIT."
            ))

        # Detección de uso de threading en lugar de multiprocessing
        if "threading.Thread" in content and ("numpy" in content or "math" in content):
            issues.append(LintIssue(
                rule_id="PY-002",
                severity=LintSeverity.WARNING,
                message="Uso de 'threading' para cómputo numérico. En Python el GIL impedirá el paralelismo real en los núcleos AMD Ryzen. Usar 'multiprocessing' o 'ProcessPoolExecutor'.",
                suggestion="from concurrent.futures import ProcessPoolExecutor"
            ))

        # Detección de escritura a disco en cada iteración
        if re.search(r"for\s+\w+\s+in\s+.+?:\s*\n[\s\S]{1,200}(?:open\(|\.write\(|\.to_csv\()", content):
            issues.append(LintIssue(
                rule_id="PY-003",
                severity=LintSeverity.WARNING,
                message="Operaciones de escritura a disco (I/O) dentro de un bucle de simulación. Esto degrada fuertemente el rendimiento por latencia de almacenamiento.",
                suggestion="Acumular en memoria en arrays NumPy o listas y volcar por lotes a /scratch o en formato binario HDF5/FITS."
            ))

        return issues

    def fix(self, file_path: str, content: str) -> Tuple[str, List[str]]:
        # Las optimizaciones de código de simulación son asistidas por IA y no determinísticas ciegas
        return content, []

    def extract_code_suggestions(self, file_path: str, content: str) -> List[CodeOptimizationSuggestion]:
        """Extrae propuestas de mejora estructuradas con bloques de código antes/después."""
        suggestions: List[CodeOptimizationSuggestion] = []

        # Ejemplo de bucle for nativo a vectorizado
        if re.search(r"for\s+i\s+in\s+range\(.+?\):\s*\n\s+.*append\(", content):
            suggestions.append(CodeOptimizationSuggestion(
                file_path=file_path,
                category="Vectorización NumPy / Rendimiento",
                title="Sustitución de bucles de acumulación por arrays NumPy",
                explanation=(
                    "En los nodos AMD Ryzen 9 5950X/5900X del IALP, las instrucciones AVX2 de la arquitectura Zen 3 "
                    "permiten procesar múltiples elementos por ciclo de reloj cuando se emplean arreglos NumPy contiguos, "
                    "eliminando el overhead de interpretación de objetos dinámicos de Python."
                ),
                original_code="# Bucle nativo lento\nresultados = []\nfor i in range(N):\n    resultados.append(calcular_energia(datos[i]))",
                optimized_code="# Vectorizado con NumPy\nimport numpy as np\n# Operar directamente sobre el array completo aprovechando SIMD / AVX2\nresultados = np.vectorize(calcular_energia)(datos)",
                estimated_gain="10x a 40x de aceleración"
            ))

        if "threading.Thread" in content:
            suggestions.append(CodeOptimizationSuggestion(
                file_path=file_path,
                category="Paralelismo Real sin GIL",
                title="Migración de Threading a ProcessPoolExecutor",
                explanation=(
                    "Debido al Global Interpreter Lock (GIL) de CPython, los hilos comparten un solo núcleo físico. "
                    "Para aprovechar los 16 núcleos del Ryzen 5950X o 12 del 5900X, cada tarea debe ser un proceso independiente."
                ),
                original_code="import threading\nt = threading.Thread(target=simular, args=(chunk,))\nt.start()",
                optimized_code="from concurrent.futures import ProcessPoolExecutor\nimport os\n\n# Aprovechar los cores asignados por Slurm\nnum_workers = int(os.environ.get('SLURM_CPUS_PER_TASK', 16))\nwith ProcessPoolExecutor(max_workers=num_workers) as executor:\n    resultados = list(executor.map(simular, chunks))",
                estimated_gain="Hasta 14x de aceleración en nodos Ryzen"
            ))

        return suggestions
