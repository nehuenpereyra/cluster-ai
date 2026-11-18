"""modules/linters/sbatch_linter.py - Validador y corrector de directivas Slurm para el clúster IALP."""

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from modules.linters.base import BaseLinter
from shared.models import LintIssue, LintSeverity
from shared.sbatch import Directive, SbatchScript, format_directive, parse_sbatch
from shared.utils import SBATCH_TIME_UNLIMITED, parse_sbatch_time_limit

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "cluster.yaml"

# Valores de respaldo si no se puede leer config/cluster.yaml (colas reales del servidor)
FALLBACK_PARTITIONS = {"short": 4 * 3600, "medium": 24 * 3600, "long": 7 * 24 * 3600}
FALLBACK_DEFAULT_PARTITION = "medium"
FALLBACK_PYTHON_MODULE = "python/3.12"
FALLBACK_MPI_MODULES = ["mpi/openmpi-x86_64", "mpi/mpich-x86_64", "intel/mpi/2021.14"]

ADDED_MARK = "# añadida por Cluster-AI"


@dataclass
class ClusterPolicy:
    """Colas, límites y módulos del clúster, leídos de config/cluster.yaml."""
    partitions: Dict[str, int]                 # nombre -> MaxTime en segundos (SBATCH_TIME_UNLIMITED = sin límite)
    default_partition: str
    max_cores_per_node: int                    # núcleos físicos del nodo más grande
    min_cores_per_node: int                    # núcleos físicos del nodo más chico
    python_module: str
    mpi_modules: List[str] = field(default_factory=list)
    restricted: set = field(default_factory=set)   # colas que requieren autorización: válidas, nunca propuestas
    max_cpus_per_node: int = 0                 # CPUs de Slurm (CPUTot, cuenta hilos SMT) del nodo más grande
    min_cpus_per_node: int = 0
    large_nodes: List[str] = field(default_factory=list)  # nodos con max_cpus_per_node

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "ClusterPolicy":
        data: dict = {}
        path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            data = {}

        partitions: Dict[str, int] = {}
        restricted = set()
        default_partition = FALLBACK_DEFAULT_PARTITION
        for name, info in (data.get("partitions") or {}).items():
            info = info or {}
            seconds = info.get("max_time_seconds")
            if seconds is None:
                seconds = parse_sbatch_time_limit(str(info.get("max_time", ""))) or SBATCH_TIME_UNLIMITED
            partitions[name] = int(seconds)
            if info.get("restricted"):
                restricted.add(name)
            if info.get("is_default"):
                default_partition = name
        if not partitions:
            partitions = dict(FALLBACK_PARTITIONS)

        nodes = [n for n in (data.get("nodes", {}) or {}).get("compute_nodes", []) if n.get("cores")]
        cores = [int(n["cores"]) for n in nodes]
        cpus = [int(n.get("slurm_cpus") or n.get("threads") or n["cores"]) for n in nodes]
        max_cpus = max(cpus) if cpus else 32
        modules = data.get("software_modules") or {}
        return cls(
            partitions=partitions,
            default_partition=default_partition if default_partition in partitions else next(iter(partitions)),
            max_cores_per_node=max(cores) if cores else 16,
            min_cores_per_node=min(cores) if cores else 12,
            python_module=modules.get("python") or FALLBACK_PYTHON_MODULE,
            mpi_modules=list(modules.get("mpi") or FALLBACK_MPI_MODULES),
            restricted=restricted,
            max_cpus_per_node=max_cpus,
            min_cpus_per_node=min(cpus) if cpus else 24,
            large_nodes=[n.get("name", "?") for n, c in zip(nodes, cpus) if c == max_cpus],
        )

    def fits(self, partition: str, seconds: Optional[int]) -> bool:
        limit = self.partitions.get(partition)
        if limit is None:
            return False
        if seconds is None or limit == SBATCH_TIME_UNLIMITED:
            return True
        if seconds == SBATCH_TIME_UNLIMITED:
            return False
        return seconds <= limit

    def smallest_fitting(self, seconds: Optional[int]) -> Optional[str]:
        """Cola abierta más corta cuyo límite admite el tiempo pedido (None si ninguna)."""
        if seconds is None:
            return self.default_partition
        ordered = sorted(
            ((n, l) for n, l in self.partitions.items() if n not in self.restricted),
            key=lambda kv: math.inf if kv[1] == SBATCH_TIME_UNLIMITED else kv[1],
        )
        for name, _ in ordered:
            if self.fits(name, seconds):
                return name
        return None

    def restricted_fitting(self, seconds: Optional[int]) -> Optional[str]:
        """Cola restringida que admitiría el tiempo pedido (solo para mencionarla)."""
        for name in sorted(self.restricted):
            if self.fits(name, seconds):
                return name
        return None

    def describe(self) -> str:
        parts = []
        for name, limit in self.partitions.items():
            label = "sin límite" if limit == SBATCH_TIME_UNLIMITED else f"hasta {_format_limit(limit)}"
            suffix = ", requiere autorización" if name in self.restricted else ""
            parts.append(f"{name} ({label}{suffix})")
        return ", ".join(parts)


def _format_limit(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours = rem // 3600
    if days and not hours:
        return f"{days} día{'s' if days != 1 else ''}"
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h"


def _int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    m = re.match(r"^(\d+)", value.strip())
    return int(m.group(1)) if m else None


def _has_unique_id(pattern: str) -> bool:
    return "%j" in pattern or "%A" in pattern


def _with_job_id(pattern: str) -> str:
    """Inserta '-%j' antes de la extensión: 'logs/salida.txt' -> 'logs/salida-%j.txt'."""
    head, sep, name = pattern.rpartition("/")
    stem, dot, ext = name.rpartition(".")
    if dot and stem:
        name = f"{stem}-%j.{ext}"
    else:
        name = f"{name}-%j"
    return f"{head}{sep}{name}"


class SbatchLinter(BaseLinter):
    """
    Valida y optimiza scripts #SBATCH adaptándolos al hardware y a las colas del clúster IALP.

    Las correcciones automáticas (`fix`) se limitan a cambios seguros sobre las directivas:
    cola inválida o incompatible con el tiempo, nombres de log sin %j, directivas faltantes de
    salida/error y directivas que Slurm ignoraba por estar después de los comandos. Nunca agrega
    tiempo límite, no altera cores/tareas ni inserta comandos (como `module load`): eso queda como
    recomendación en el informe.
    """

    def __init__(self, policy: Optional[ClusterPolicy] = None):
        self.policy = policy or ClusterPolicy.load()

    def can_handle(self, file_path: str, content: str) -> bool:
        return "#SBATCH" in content or file_path.endswith((".sh", ".sbatch", ".slurm"))

    # ------------------------------------------------------------------ análisis

    def _requested_seconds(self, script: SbatchScript) -> Tuple[Optional[int], Optional[Directive]]:
        d = script.get("time")
        if d is None or d.value is None:
            return None, d
        return parse_sbatch_time_limit(d.value), d

    def _commands_text(self, script: SbatchScript) -> str:
        return "\n".join(line for _, line in script.command_lines)

    def lint(self, file_path: str, content: str) -> List[LintIssue]:
        issues: List[LintIssue] = []
        script = parse_sbatch(content)
        policy = self.policy

        # 0. Directivas que Slurm ignora por estar después del primer comando
        for d in script.ignored:
            issues.append(LintIssue(
                rule_id="SLURM-004",
                severity=LintSeverity.WARNING,
                line=d.line_number,
                message=(f"La directiva '{d.raw}' está después de la primera línea de comandos "
                         f"(línea {script.header_end + 1}); Slurm la ignora."),
                suggestion="Mover todas las líneas #SBATCH al principio del script, antes de cualquier comando."
            ))

        # 1. Cola
        seconds, time_d = self._requested_seconds(script)
        part_d = script.get("partition")
        partitions = [p for p in (part_d.value or "").split(",") if p] if part_d else []
        invalid = [p for p in partitions if p not in policy.partitions]
        if not part_d:
            issues.append(LintIssue(
                rule_id="IALP-001",
                severity=LintSeverity.INFO,
                message=f"No se especificó cola (#SBATCH -p). Slurm usará la predeterminada '{policy.default_partition}'.",
                suggestion=f"#SBATCH -p {policy.smallest_fitting(seconds) or policy.default_partition}"
            ))
        elif invalid:
            issues.append(LintIssue(
                rule_id="IALP-001",
                severity=LintSeverity.ERROR,
                line=part_d.line_number,
                message=(f"La cola '{', '.join(invalid)}' no existe en el clúster. "
                         f"Colas disponibles: {policy.describe()}. Slurm rechazará el trabajo."),
                suggestion=f"#SBATCH -p {policy.smallest_fitting(seconds) or policy.default_partition}"
            ))
        restricted_used = [p for p in partitions if p in policy.restricted]
        if restricted_used:
            issues.append(LintIssue(
                rule_id="IALP-001",
                severity=LintSeverity.INFO,
                line=part_d.line_number,
                message=(f"La cola '{', '.join(restricted_used)}' es restringida: requiere autorización del "
                         f"administrador. Si su usuario no está habilitado, Slurm rechazará el trabajo."),
                suggestion=None
            ))

        # 2. Tiempo límite
        if time_d is None:
            issues.append(LintIssue(
                rule_id="SLURM-002",
                severity=LintSeverity.WARNING,
                message=("No se especificó tiempo límite (#SBATCH -t). El trabajo recibirá el máximo de la cola, "
                         "lo que puede demorar su inicio. Indicar una estimación realista (con margen)."),
                suggestion="#SBATCH -t HH:MM:SS"
            ))
        elif seconds is None:
            issues.append(LintIssue(
                rule_id="SLURM-002",
                severity=LintSeverity.ERROR,
                line=time_d.line_number,
                message=f"Formato de tiempo inválido '{time_d.value}'. Formatos válidos: M, M:S, H:M:S, D-H, D-H:M, D-H:M:S.",
                suggestion="#SBATCH -t 02:00:00"
            ))
        elif partitions and not invalid:
            if not any(policy.fits(p, seconds) for p in partitions):
                target = policy.smallest_fitting(seconds)
                restricted_target = policy.restricted_fitting(seconds)
                if target:
                    hint = f"Cambiar a la cola '{target}'."
                elif restricted_target:
                    hint = (f"Ninguna cola abierta admite ese tiempo: dividir la simulación con checkpoints, o solicitar "
                            f"al administrador acceso a la cola restringida '{restricted_target}'.")
                else:
                    hint = "Ninguna cola admite ese tiempo: reducirlo o dividir la simulación con checkpoints."
                issues.append(LintIssue(
                    rule_id="IALP-002",
                    severity=LintSeverity.ERROR,
                    line=time_d.line_number,
                    message=f"El tiempo solicitado ({time_d.value}) supera el límite de la cola '{part_d.value}'. Slurm no lo iniciará.",
                    suggestion=hint
                ))
        elif not partitions and not policy.fits(policy.default_partition, seconds):
            target = policy.smallest_fitting(seconds)
            issues.append(LintIssue(
                rule_id="IALP-002",
                severity=LintSeverity.ERROR,
                line=time_d.line_number,
                message=(f"El tiempo solicitado ({time_d.value}) supera el límite de la cola predeterminada "
                         f"'{policy.default_partition}'."),
                suggestion=f"#SBATCH -p {target}" if target else "Reducir el tiempo solicitado."
            ))

        # 3. Logs con %j
        out_d, err_d = script.get("output"), script.get("error")
        if out_d is None or not out_d.value or not _has_unique_id(out_d.value):
            issues.append(LintIssue(
                rule_id="SLURM-001",
                severity=LintSeverity.WARNING,
                line=out_d.line_number if out_d else None,
                message="El archivo de salida (-o) debe definirse e incluir '%j' para no mezclar los logs de distintas ejecuciones.",
                suggestion=f"#SBATCH -o {_with_job_id(out_d.value) if out_d and out_d.value else 'salida-%j.out'}"
            ))
        if err_d is None or not err_d.value or not _has_unique_id(err_d.value):
            if err_d is not None and err_d.value:
                err_suggestion = _with_job_id(err_d.value)
            elif out_d is not None and out_d.value and out_d.value.endswith(".out"):
                err_suggestion = (out_d.value if _has_unique_id(out_d.value) else _with_job_id(out_d.value))[:-4] + ".err"
            else:
                err_suggestion = "salida-%j.err"
            issues.append(LintIssue(
                rule_id="SLURM-001",
                severity=LintSeverity.WARNING,
                line=err_d.line_number if err_d else None,
                message="El archivo de error (-e) debe definirse e incluir '%j' para la trazabilidad de errores.",
                suggestion=f"#SBATCH -e {err_suggestion}"
            ))

        # 4. CPUs por nodo vs capacidad real de Slurm (CPUTot cuenta hilos SMT: 32 o 24 por nodo)
        nodes = _int(script.value("nodes")) or 1
        ntasks = _int(script.value("ntasks"))
        ntasks_per_node = _int(script.value("ntasks-per-node"))
        cpus_per_task = _int(script.value("cpus-per-task")) or 1
        if ntasks_per_node:
            cpus_per_node = ntasks_per_node * cpus_per_task
        elif ntasks:
            cpus_per_node = math.ceil(ntasks / nodes) * cpus_per_task
        else:
            cpus_per_node = cpus_per_task
        large = ", ".join(policy.large_nodes) or "los nodos 5950X"
        if cpus_per_node > policy.max_cpus_per_node:
            issues.append(LintIssue(
                rule_id="IALP-003",
                severity=LintSeverity.ERROR,
                message=(f"Se solicitan {cpus_per_node} CPUs por nodo, pero los nodos más grandes tienen "
                         f"{policy.max_cpus_per_node} CPUs en Slurm ({policy.max_cores_per_node} núcleos con SMT). "
                         f"El trabajo quedará PENDING indefinidamente."),
                suggestion=f"Repartir en más nodos (-N) para no superar {policy.max_cpus_per_node} CPUs por nodo."
            ))
        elif cpus_per_node > policy.min_cpus_per_node:
            issues.append(LintIssue(
                rule_id="IALP-003",
                severity=LintSeverity.INFO,
                message=(f"Se solicitan {cpus_per_node} CPUs por nodo: solo cabe en {large} "
                         f"({policy.max_cpus_per_node} CPUs), no en los nodos de {policy.min_cpus_per_node} CPUs. "
                         f"Puede esperar más en cola."),
                suggestion=None
            ))
        if policy.max_cores_per_node < cpus_per_node <= policy.max_cpus_per_node:
            issues.append(LintIssue(
                rule_id="IALP-005",
                severity=LintSeverity.INFO,
                message=(f"{cpus_per_node} CPUs por nodo supera los {policy.max_cores_per_node} núcleos físicos: "
                         f"se usarán los hilos SMT. Para cómputo intensivo (MPI, numérico) suele rendir igual o peor "
                         f"que un proceso por núcleo; medir con `cluster-ai short-run` antes de una corrida larga."),
                suggestion=f"Probar con {policy.max_cores_per_node} tareas por nodo."
            ))

        # 5. Módulos de entorno (solo comandos reales, no comentarios)
        commands = self._commands_text(script)
        lower = commands.lower()
        loads = re.findall(r"(?:^|[;&|\s])(?:module\s+(?:load|add)|ml)\s+([^\n;&|]+)", lower)
        loaded = " ".join(loads)
        env_managed = bool(re.search(r"\bconda\s+activate\b|\bsource\s+\S*activate\b|\bmamba\s+activate\b", lower))

        if re.search(r"(?:^|[;&|\s/])python[0-9.]*(?:\s|$)", lower) and "python" not in loaded and not env_managed:
            issues.append(LintIssue(
                rule_id="SLURM-003",
                severity=LintSeverity.WARNING,
                message=("El script ejecuta Python sin cargar un módulo ni activar un entorno: usará el Python del "
                         "sistema de los nodos, que puede no tener las librerías necesarias."),
                suggestion=f"module load {policy.python_module}"
            ))

        mpi_loaded = "mpi" in loaded
        mpi_options = " o ".join(policy.mpi_modules)
        if re.search(r"\b(?:mpirun|mpiexec)\b", lower) and not mpi_loaded:
            issues.append(LintIssue(
                rule_id="SLURM-003",
                severity=LintSeverity.WARNING,
                message="El script usa mpirun/mpiexec sin cargar un módulo MPI.",
                suggestion=f"module load <el MPI con que se compiló: {mpi_options}>"
            ))
        elif re.search(r"\bsrun\b", lower) and (ntasks or (ntasks_per_node or 1) * nodes) > 1 and not mpi_loaded:
            issues.append(LintIssue(
                rule_id="MPI-001",
                severity=LintSeverity.INFO,
                message=("Se lanzan varias tareas con srun sin cargar un módulo MPI. Si el programa usa MPI, "
                         "cargue el mismo MPI con el que fue compilado."),
                suggestion=f"module load {policy.mpi_modules[0] if policy.mpi_modules else 'mpi/<implementación>'}"
            ))

        return issues

    # ------------------------------------------------------------------ correcciones

    def _target_partition(self, script: SbatchScript, seconds: Optional[int]) -> Optional[str]:
        """Cola que debería quedar en el script optimizado (None si no hay que tocarla)."""
        policy = self.policy
        part_d = script.get("partition")
        if part_d is None:
            # Solo se agrega si hay un tiempo conocido: la más corta que lo admite
            return policy.smallest_fitting(seconds) if seconds is not None else None
        partitions = [p for p in (part_d.value or "").split(",") if p]
        if partitions and all(p in policy.partitions for p in partitions) and any(policy.fits(p, seconds) for p in partitions):
            return None
        return policy.smallest_fitting(seconds)

    def recommended_partition(self, content: str) -> str:
        """Cola efectiva del script (la indicada, o la predeterminada del clúster)."""
        script = parse_sbatch(content)
        value = script.value("partition")
        if value:
            return value.split(",")[0]
        return self.policy.default_partition

    def fix(self, file_path: str, content: str) -> Tuple[str, List[str]]:
        changes: List[str] = []
        script = parse_sbatch(content)
        lines = list(script.lines)
        policy = self.policy

        # 1. Directivas ignoradas por estar después de los comandos: se mueven a la cabecera
        #    (salvo que la misma opción ya esté definida en la cabecera).
        header_names = {d.name for d in script.directives}
        lines_to_move: List[int] = []
        for idx in sorted({d.line_index for d in script.ignored}):
            line_dirs = [d for d in script.ignored if d.line_index == idx]
            if any(d.name in header_names for d in line_dirs):
                continue
            lines_to_move.append(idx)
            header_names.update(d.name for d in line_dirs)
        moved_lines = [lines[idx] for idx in lines_to_move]
        for idx in lines_to_move:
            changes.append(f"Directiva movida a la cabecera (Slurm la ignoraba en la línea {idx + 1}): {lines[idx].strip()}")

        # Script efectivo tras mover (para decidir el resto de los cambios)
        effective = parse_sbatch("\n".join(
            lines[:script.header_end] + moved_lines + ["true"]
        ))
        seconds = parse_sbatch_time_limit(effective.value("time") or "") if effective.value("time") else None

        # 2. Ediciones en el lugar (cola y nombres de log), sobre las líneas originales
        edits: Dict[int, List[Tuple[Tuple[int, int], str]]] = {}

        def edit(d: Directive, new_text: str) -> None:
            edits.setdefault(d.line_index, []).append((d.span, new_text))

        additions: List[str] = []

        target = self._target_partition(effective, seconds)
        part_d = effective.get("partition")
        if target is not None:
            if part_d is not None:
                original = self._original_directive(script, part_d, lines_to_move)
                edit(original, format_directive("partition", target, original.style))
                changes.append(f"Cola cambiada de '{part_d.value}' a '{target}' (válida y compatible con el tiempo solicitado).")
            else:
                additions.append(f"#SBATCH -p {target}  {ADDED_MARK}: cola más corta que admite el tiempo pedido")
                changes.append(f"Directiva añadida: #SBATCH -p {target}")

        out_d, err_d = effective.get("output"), effective.get("error")
        stem = Path(file_path).stem or "salida"
        if out_d is not None and out_d.value and not _has_unique_id(out_d.value):
            new_value = _with_job_id(out_d.value)
            original = self._original_directive(script, out_d, lines_to_move)
            edit(original, format_directive("output", new_value, original.style))
            changes.append(f"Archivo de salida con JobID: '{out_d.value}' -> '{new_value}'.")
        out_value = (_with_job_id(out_d.value) if out_d is not None and out_d.value and not _has_unique_id(out_d.value)
                     else (out_d.value if out_d is not None and out_d.value else None))
        if out_d is None:
            out_value = f"{stem}-%j.out"
            additions.append(f"#SBATCH -o {out_value}  {ADDED_MARK}")
            changes.append(f"Directiva añadida: #SBATCH -o {out_value}")

        if err_d is not None and err_d.value and not _has_unique_id(err_d.value):
            new_value = _with_job_id(err_d.value)
            original = self._original_directive(script, err_d, lines_to_move)
            edit(original, format_directive("error", new_value, original.style))
            changes.append(f"Archivo de error con JobID: '{err_d.value}' -> '{new_value}'.")
        elif err_d is None:
            if out_value and out_value.endswith(".out"):
                err_value = out_value[:-4] + ".err"
            else:
                err_value = f"{stem}-%j.err"
            additions.append(f"#SBATCH -e {err_value}  {ADDED_MARK}")
            changes.append(f"Directiva añadida: #SBATCH -e {err_value}")

        # Aplicar ediciones (de derecha a izquierda para conservar posiciones)
        for idx, line_edits in edits.items():
            line = lines[idx]
            for (start, end), new_text in sorted(line_edits, key=lambda e: e[0][0], reverse=True):
                line = line[:start] + new_text + line[end:]
            lines[idx] = line

        if not changes:
            return content if content.endswith("\n") else content + "\n", changes

        # 3. Reconstruir: cabecera + directivas movidas/añadidas + resto (sin las líneas movidas)
        insert_at = (script.last_directive_line + 1) if script.last_directive_line is not None else (
            1 if lines and lines[0].startswith("#!") else 0)
        insert_at = min(insert_at, script.header_end)
        moved_now = [lines[idx] for idx in lines_to_move]
        block = moved_now + additions
        rest = [l for i, l in enumerate(lines) if i not in set(lines_to_move)]
        # Recalcular el punto de inserción sobre la lista sin las líneas movidas (todas posteriores)
        new_lines = rest[:insert_at] + block + rest[insert_at:]
        return "\n".join(new_lines) + "\n", changes

    @staticmethod
    def _original_directive(
        script: SbatchScript,
        effective_d: Directive,
        lines_to_move: List[int],
    ) -> Directive:
        """Traduce una directiva del script efectivo a su línea original."""
        if effective_d.line_index < script.header_end:
            return effective_d
        # Proviene de una línea movida: su índice en `effective` es header_end + posición
        pos = effective_d.line_index - script.header_end
        original_idx = lines_to_move[pos]
        return Directive(
            original_idx, effective_d.name, effective_d.value, effective_d.span,
            effective_d.style, effective_d.raw, effective_d.known,
        )
