"""modules/linters/mpi_linter.py - Validador de paralelismo MPI y OpenMP para clúster IALP."""

import re
from typing import List, Tuple
from modules.linters.base import BaseLinter
from shared.models import LintIssue, LintSeverity

class MPILinter(BaseLinter):
    """Verifica la invocación de MPI y la afinidad de hilos OpenMP."""

    def can_handle(self, file_path: str, content: str) -> bool:
        lower = content.lower()
        return "mpi" in lower or "mpirun" in lower or "omp_num_threads" in lower

    def lint(self, file_path: str, content: str) -> List[LintIssue]:
        issues: List[LintIssue] = []

        if "mpirun" in content and "OMP_NUM_THREADS" in content and "srun" not in content:
            issues.append(LintIssue(
                rule_id="MPI-001",
                severity=LintSeverity.INFO,
                message="En Slurm, 'srun' suele integrarse mejor con el gestor de recursos y la asignación cgroup de núcleos que 'mpirun' directo.",
                suggestion="srun --mpi=pmix ./ejecutable"
            ))

        if "OMP_NUM_THREADS" not in content and ("omp" in content.lower() or "openmp" in content.lower()):
            issues.append(LintIssue(
                rule_id="MPI-002",
                severity=LintSeverity.WARNING,
                message="El código menciona OpenMP pero no define 'export OMP_NUM_THREADS'. Podría intentar usar todos los hilos del nodo colapsando el rendimiento de caché L3.",
                suggestion="export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK"
            ))

        return issues

    def fix(self, file_path: str, content: str) -> Tuple[str, List[str]]:
        return content, []
