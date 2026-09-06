"""modules/linters/sbatch_linter.py - Validador y corrector de directivas Slurm para el clúster IALP."""

import re
from typing import List, Tuple
from modules.linters.base import BaseLinter
from shared.models import LintIssue, LintSeverity
from shared.utils import parse_slurm_time

VALID_PARTITIONS = ["short", "medium", "long", "prolonged"]
PARTITION_TIME_LIMITS = {
    "short": 4 * 3600,         # 4 horas
    "medium": 24 * 3600,       # 24 horas
    "long": 7 * 24 * 3600,     # 7 días
    "prolonged": 30 * 24 * 3600 # 30 días
}

class SbatchLinter(BaseLinter):
    """
    Valida y optimiza scripts #SBATCH adaptándolos específicamente al hardware y
    políticas de colas del clúster IALP (CONICET / UNLP).
    """

    def can_handle(self, file_path: str, content: str) -> bool:
        return "#SBATCH" in content or file_path.endswith((".sh", ".sbatch", ".slurm"))

    def lint(self, file_path: str, content: str) -> List[LintIssue]:
        issues: List[LintIssue] = []
        lines = content.splitlines()

        has_partition = False
        partition_val = "medium"  # default
        partition_line = None

        has_time = False
        time_val = None
        time_line = None

        has_output = False
        output_has_jobid = False

        has_error = False
        error_has_jobid = False

        nodes_count = 1
        cores_count = 1

        for idx, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line.startswith("#SBATCH"):
                continue

            # Partición
            m_part = re.search(r"(?:-p|--partition=)\s*([^\s#]+)", line)
            if m_part:
                has_partition = True
                partition_val = m_part.group(1)
                partition_line = idx

            # Tiempo límite
            m_time = re.search(r"(?:-t|--time=)\s*([^\s#]+)", line)
            if m_time:
                has_time = True
                time_val = m_time.group(1)
                time_line = idx

            # Salida stdout
            m_out = re.search(r"(?:-o|--output=)\s*([^\s#]+)", line)
            if m_out:
                has_output = True
                if "%j" in m_out.group(1):
                    output_has_jobid = True

            # Salida stderr
            m_err = re.search(r"(?:-e|--error=)\s*([^\s#]+)", line)
            if m_err:
                has_error = True
                if "%j" in m_err.group(1):
                    error_has_jobid = True

            # Cores y nodos
            m_nodes = re.search(r"(?:-N|--nodes=)\s*(\d+)", line)
            if m_nodes:
                nodes_count = int(m_nodes.group(1))

            m_cores = re.search(r"(?:-n|--ntasks=|-c|--cpus-per-task=)\s*(\d+)", line)
            if m_cores:
                cores_count = int(m_cores.group(1))

        # 1. Validación de partición
        if not has_partition:
            issues.append(LintIssue(
                rule_id="IALP-001",
                severity=LintSeverity.WARNING,
                message="No se especificó cola (#SBATCH -p). El clúster asignará 'medium' por defecto.",
                suggestion="#SBATCH -p medium"
            ))
        elif partition_val not in VALID_PARTITIONS:
            issues.append(LintIssue(
                rule_id="IALP-001",
                severity=LintSeverity.ERROR,
                line=partition_line,
                message=f"La cola '{partition_val}' no existe en el clúster IALP. Colas válidas: {', '.join(VALID_PARTITIONS)}.",
                suggestion="#SBATCH -p medium"
            ))

        # 2. Validación de tiempo límite vs cola
        if has_time and time_val:
            total_sec = parse_slurm_time(time_val)
            max_sec = PARTITION_TIME_LIMITS.get(partition_val, 24 * 3600)
            if total_sec > max_sec:
                issues.append(LintIssue(
                    rule_id="IALP-002",
                    severity=LintSeverity.ERROR,
                    line=time_line,
                    message=f"El tiempo solicitado ({time_val}) supera el límite de la cola '{partition_val}'. Su trabajo será cancelado antes de finalizar.",
                    suggestion=f"Ajustar #SBATCH -t o cambiar a una cola superior (ej. 'long' o 'prolonged')."
                ))
        elif not has_time:
            issues.append(LintIssue(
                rule_id="SLURM-002",
                severity=LintSeverity.WARNING,
                message="No se especificó tiempo límite (#SBATCH -t). Se recomienda definirlo para evitar bloqueos.",
                suggestion="#SBATCH -t 01:00:00"
            ))

        # 3. Validación de logs con %j
        if not has_output or not output_has_jobid:
            issues.append(LintIssue(
                rule_id="SLURM-001",
                severity=LintSeverity.WARNING,
                message="El archivo de salida (-o) debe contener '%j' para evitar que se sobrescriban los logs de diferentes ejecuciones.",
                suggestion="#SBATCH -o salida-%j.out"
            ))

        if not has_error or not error_has_jobid:
            issues.append(LintIssue(
                rule_id="SLURM-001",
                severity=LintSeverity.WARNING,
                message="El archivo de error (-e) debe contener '%j' para trazabilidad de errores.",
                suggestion="#SBATCH -e salida-%j.err"
            ))

        # 4. Cores vs arquitectura Ryzen
        if nodes_count == 1 and cores_count > 16:
            issues.append(LintIssue(
                rule_id="IALP-003",
                severity=LintSeverity.ERROR,
                message=f"Se solicitaron {cores_count} núcleos en un solo nodo. Los nodos más grandes del IALP (Ryzen 9 5950X) poseen 16 núcleos físicos.",
                suggestion="Reducir cores a <= 16 por nodo o solicitar múltiples nodos (-N 2)."
            ))
        elif nodes_count == 1 and cores_count > 12:
            issues.append(LintIssue(
                rule_id="IALP-003",
                severity=LintSeverity.INFO,
                message=f"Se solicitaron {cores_count} cores. Este trabajo solo podrá asignarse en los nodos 5950X (16c), excluyendo los 5900X (12c).",
                suggestion=None
            ))

        # 5. Módulos de entorno
        lower_content = content.lower()
        if "python" in lower_content and "module load python" not in lower_content:
            issues.append(LintIssue(
                rule_id="SLURM-003",
                severity=LintSeverity.WARNING,
                message="El script invoca Python pero no contiene 'module load python3'.",
                suggestion="module load python3"
            ))

        if ("mpirun" in lower_content or "srun" in lower_content) and "module load openmpi" not in lower_content:
            issues.append(LintIssue(
                rule_id="SLURM-003",
                severity=LintSeverity.WARNING,
                message="El script utiliza MPI pero no carga el módulo correspondiente.",
                suggestion="module load openmpi"
            ))

        return issues

    def fix(self, file_path: str, content: str) -> Tuple[str, List[str]]:
        changes: List[str] = []
        lines = content.splitlines()
        new_lines: List[str] = []

        has_partition = any(re.search(r"#(?:SBATCH)\s+(?:-p|--partition=)", l) for l in lines)
        has_output = any(re.search(r"#(?:SBATCH)\s+(?:-o|--output=)", l) for l in lines)
        has_error = any(re.search(r"#(?:SBATCH)\s+(?:-e|--error=)", l) for l in lines)
        has_time = any(re.search(r"#(?:SBATCH)\s+(?:-t|--time=)", l) for l in lines)

        # Detectar tiempo y cores solicitados
        time_val = "01:00:00"
        cores_val = 1
        nodes_val = 1
        for l in lines:
            m_t = re.search(r"(?:-t|--time=)\s*([^\s#]+)", l)
            if m_t:
                time_val = m_t.group(1)
            m_c = re.search(r"(?:-n|--ntasks=|-c|--cpus-per-task=)\s*(\d+)", l)
            if m_c:
                cores_val = int(m_c.group(1))
            m_n = re.search(r"(?:-N|--nodes=)\s*(\d+)", l)
            if m_n:
                nodes_val = int(m_n.group(1))

        total_sec = parse_slurm_time(time_val)
        recommended_part = "medium"
        if total_sec <= 4 * 3600:
            recommended_part = "short"
        elif total_sec <= 24 * 3600:
            recommended_part = "medium"
        elif total_sec <= 7 * 24 * 3600:
            recommended_part = "long"
        else:
            recommended_part = "prolonged"

        sbatch_section_ended = False
        shebang_seen = False

        for l in lines:
            if l.startswith("#!"):
                shebang_seen = True
                new_lines.append(l)
                continue

            if l.strip().startswith("#SBATCH"):
                # Corregir partición inválida o incompatible con el tiempo
                if re.search(r"(?:-p|--partition=)", l):
                    curr_part = re.search(r"(?:-p|--partition=)\s*([^\s#]+)", l).group(1)
                    if curr_part not in VALID_PARTITIONS or total_sec > PARTITION_TIME_LIMITS.get(curr_part, 86400):
                        l = re.sub(r"(?:-p|--partition=)\s*([^\s#]+)", f"-p {recommended_part}", l)
                        changes.append(f"Cola ajustada a '{recommended_part}' compatible con el tiempo estimado ({time_val}).")

                # Corregir cores si supera el máximo físico de 1 nodo (16 cores en 5950X)
                if nodes_val == 1 and cores_val > 16 and re.search(r"(?:-n|--ntasks=|-c|--cpus-per-task=)", l):
                    l = re.sub(r"(?:-n|--ntasks=|-c|--cpus-per-task=)\s*(\d+)", "-n 16", l)
                    changes.append("Núcleos ajustados a 16 (máximo físico disponible por nodo en el Ryzen 9 5950X).")

                # Corregir salida con %j
                if re.search(r"(?:-o|--output=)", l) and "%j" not in l:
                    l = re.sub(r"(?:-o|--output=)\s*([^\s#]+)", "-o salida-%j.out", l)
                    changes.append("Archivo de salida estandarizado a 'salida-%j.out'.")
                if re.search(r"(?:-e|--error=)", l) and "%j" not in l:
                    l = re.sub(r"(?:-e|--error=)\s*([^\s#]+)", "-e salida-%j.err", l)
                    changes.append("Archivo de error estandarizado a 'salida-%j.err'.")

                new_lines.append(l)
            else:
                if not sbatch_section_ended and shebang_seen:
                    # Inyectar directivas faltantes antes de salir de la cabecera sbatch
                    if not has_partition:
                        new_lines.append(f"#SBATCH -p {recommended_part} # Asignada automáticamente según tiempo estimado")
                        changes.append(f"Directiva añadida: #SBATCH -p {recommended_part}")
                    if not has_output:
                        new_lines.append("#SBATCH -o salida-%j.out # Archivo de log individual por JobID")
                        changes.append("Directiva añadida: #SBATCH -o salida-%j.out")
                    if not has_error:
                        new_lines.append("#SBATCH -e salida-%j.err # Archivo de error individual por JobID")
                        changes.append("Directiva añadida: #SBATCH -e salida-%j.err")
                    if not has_time:
                        new_lines.append("#SBATCH -t 01:00:00 # Tiempo máximo recomendado de ejecución")
                        changes.append("Directiva añadida: #SBATCH -t 01:00:00")

                    # Inyección de módulos requeridos inmediatamente tras directivas sbatch
                    content_lower = content.lower()
                    if "python" in content_lower and "module load python" not in content_lower:
                        new_lines.append("\n# Carga de módulos según manual IALP")
                        new_lines.append("module load python3")
                        changes.append("Instrucción añadida: module load python3")
                    if ("mpi" in content_lower or "mpirun" in content_lower) and "module load openmpi" not in content_lower:
                        new_lines.append("\n# Carga de módulos según manual IALP")
                        new_lines.append("module load openmpi")
                        changes.append("Instrucción añadida: module load openmpi")

                    sbatch_section_ended = True

                new_lines.append(l)

        return "\n".join(new_lines) + "\n", changes
