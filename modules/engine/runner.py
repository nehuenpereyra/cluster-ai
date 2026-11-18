"""modules/engine/runner.py - Motor central de orquestación del pipeline de Cluster-AI."""

import os
import time
from pathlib import Path
from typing import List, Optional
from modules.engine.ai import AIClient, AgnesAIClient, OpenAICompatibleClient
from modules.engine.context import ContextAssembler
from modules.engine.privileged_client import PrivilegedClient, detect_privileged_client
from modules.engine.storage import StorageEngine
from modules.inspectors.slurm_inspector import SlurmInspector
from modules.linters.mpi_linter import MPILinter
from modules.linters.python_linter import PythonLinter
from modules.linters.sbatch_linter import SbatchLinter
from modules.reporters.console import ConsoleReporter
from modules.reporters.markdown import MarkdownReporter
from shared.exceptions import ClusterAIError
from shared.filesystem import (
    create_immutable_backup,
    generate_diff,
    safe_read_file,
    write_optimized_script,
)
from shared.features import SCOPE_FULL, SCOPE_SBATCH, analysis_scope
from shared.identity import current_user
from shared.models import AnalysisResult, BenchmarkComparison, JobRun, RunType
from shared.utils import SBATCH_TIME_UNLIMITED, compute_sha256, format_bytes, format_slurm_time, parse_sbatch_time_limit

_AUTO = object()

class EngineRunner:
    """
    Orquesta inspectores, linters, inteligencia artificial, almacenamiento y reporteros.

    En el servidor de producción (helper `cluster-ai-priv` instalado) el motor corre con la
    identidad del investigador y delega en el helper únicamente la consulta a la IA y el
    acceso a la base de datos. En desarrollo o en modo mock trabaja de forma local.
    """

    def __init__(
        self,
        storage: Optional[StorageEngine] = None,
        ai_client: Optional[OpenAICompatibleClient] = None,
        console_reporter: Optional[ConsoleReporter] = None,
        markdown_reporter: Optional[MarkdownReporter] = None,
        privileged_client: Optional[PrivilegedClient] = _AUTO  # type: ignore[assignment]
    ):
        self._custom_storage = storage
        if privileged_client is _AUTO:
            # Con un storage inyectado (pruebas) nunca se delega en el helper
            privileged_client = None if storage is not None else detect_privileged_client()
        self.privileged = privileged_client
        self.ai_client = ai_client or OpenAICompatibleClient()
        self.console = console_reporter or ConsoleReporter()
        self.markdown = markdown_reporter or MarkdownReporter()
        self.context_assembler = ContextAssembler()

        # Linters activos
        self.sbatch_linter = SbatchLinter()
        self.python_linter = PythonLinter()
        self.mpi_linter = MPILinter()

    def _get_storage(self, force_mock: bool = False) -> StorageEngine:
        """Devuelve el motor de almacenamiento apropiado (cluster.db o cluster_mock.db)."""
        if self._custom_storage:
            return self._custom_storage
        return StorageEngine(force_mock=force_mock)

    @property
    def storage(self) -> StorageEngine:
        """Acceso al storage por defecto."""
        return self._get_storage()

    def _backend(self, force_mock: bool = False) -> Optional[PrivilegedClient]:
        """Devuelve el helper privilegiado si corresponde usarlo (nunca en modo mock)."""
        if force_mock or os.getenv("CLUSTER_AI_MODE", "cluster").strip().lower() == "mock":
            return None
        return self.privileged

    def diagnose(self, script_path: str, force_mock: bool = False) -> AnalysisResult:
        """
        Ejecuta el ciclo de vida completo de diagnóstico:
        1. Respaldo inmutable del script original (.bak_<timestamp>)
        2. Análisis determinístico con linters (sbatch, python, mpi)
        3. Generación de versión optimizada (.optimized.sh)
        4. Auditoría profunda con Asistente IA (protocolo compatible con OpenAI)
        5. Persistencia en cluster.db
        6. Generación de informe técnico Markdown y visualización en consola.
        """
        path = Path(script_path).resolve()
        if not path.is_file():
            raise ClusterAIError(f"No se encontró el script a diagnosticar: {script_path}")

        original_content = safe_read_file(path)
        script_hash = compute_sha256(original_content)

        # 1. Creación inmutable de copia de respaldo
        backup_path = create_immutable_backup(path)

        # 2. Validación con Linters
        issues = self.sbatch_linter.lint(str(path), original_content)
        optimized_content, sbatch_changes = self.sbatch_linter.fix(str(path), original_content)
        sbatch_diff = generate_diff(original_content, optimized_content, fromfile=f"{path.name} (Original)", tofile=f"{path.name} (Optimizado)")

        # Guardar archivo optimizado sin tocar el original
        optimized_path = write_optimized_script(path, optimized_content)

        # Alcance: en esta fase solo se optimiza el #SBATCH. La auditoría del código fuente
        # (proyecto completo) solo puede activarse en modo local de desarrollo.
        backend = self._backend(force_mock)
        scope = SCOPE_SBATCH if backend is not None else analysis_scope()

        code_suggestions = []
        if scope == SCOPE_FULL:
            # Buscar archivos de código asociados (ej. Python) y auditarlos
            invoked_files = self.context_assembler.find_invoked_code_files(path, original_content)
            for code_file in invoked_files:
                try:
                    code_text = safe_read_file(code_file)
                    code_issues = self.python_linter.lint(str(code_file), code_text)
                    issues.extend(code_issues)
                    suggestions = self.python_linter.extract_code_suggestions(str(code_file), code_text)
                    code_suggestions.extend(suggestions)
                except Exception:
                    pass

        # Partición efectiva del script optimizado (la indicada o la predeterminada del clúster)
        recommended_part = self.sbatch_linter.recommended_partition(optimized_content)

        # 3. Consulta al Asistente IA para análisis avanzado
        issues_summary = "\n".join([f"- [{i.severity.value}] {i.rule_id}: {i.message}" for i in issues])

        if backend is not None:
            with self.console.console.status("[bold cyan]Consultando al Asistente IA (servicio Cluster-AI) para optimizar el script #SBATCH...[/bold cyan]"):
                ai_insights = backend.review_sbatch(path.name, original_content, issues_summary)
        else:
            if scope == SCOPE_FULL:
                ai_messages = self.context_assembler.assemble_ai_prompt(
                    path,
                    original_content,
                    issues_summary=issues_summary
                )
            else:
                ai_messages = self.context_assembler.assemble_sbatch_prompt(
                    path.name,
                    original_content,
                    issues_summary=issues_summary
                )
            with self.console.console.status(f"[bold cyan]Consultando al Asistente IA ({self.ai_client.model}) para optimizar el script #SBATCH...[/bold cyan]"):
                ai_insights = self.ai_client.chat_completion(ai_messages, temperature=0.6, max_tokens=2048, scope=scope)

        # 4. Consolidar resultado
        result = AnalysisResult(
            script_path=str(path),
            script_hash=script_hash,
            backup_path=str(backup_path),
            optimized_script_path=str(optimized_path),
            issues=issues,
            code_suggestions=code_suggestions,
            recommended_partition=recommended_part,
            scope=scope
        )

        # 5. Generar informe técnico en Markdown
        report_path = self.markdown.generate_report(result, ai_insights=ai_insights, sbatch_diff=sbatch_diff)
        result.report_markdown_path = str(report_path)

        # 6. Persistir en cluster.db (vía helper en el servidor) o cluster_mock.db según modo
        if backend is not None:
            backend.save_analysis(result, report_content=ai_insights)
        else:
            storage = self._get_storage(force_mock=force_mock)
            storage.save_analysis(result, markdown_content=ai_insights, user=current_user())

        # 7. Renderizar en consola
        self.console.render_analysis(result)
        return result

    def short_run(
        self,
        script_path: str,
        duration_str: str = "00:10:00",
        force_mock: Optional[bool] = None,
        wait: bool = False
    ) -> int:
        """
        Corrida corta de prueba, acotada en tiempo, para medir el consumo de CPU y memoria
        antes de enviar simulaciones que tardarían semanas o meses.

        En el clúster SIEMPRE se envía a Slurm (`sbatch --time=<duración> --partition=<cola>`):
        el nodo cabecera no se usa para cómputo. La ejecución local con telemetría simulada
        (modo mock) solo existe en equipos de desarrollo sin Slurm.
        """
        path = Path(script_path).resolve()
        if not path.is_file():
            raise ClusterAIError(f"Archivo de script no encontrado: {script_path}")

        duration_sec = parse_sbatch_time_limit(duration_str)
        if not duration_sec or duration_sec <= 0 or duration_sec == SBATCH_TIME_UNLIMITED:
            raise ClusterAIError(
                f"Duración inválida '{duration_str}'. Formatos válidos: M, H:M:S, D-H:M:S (un número solo son minutos)."
            )

        inspector = SlurmInspector(force_mock=bool(force_mock))
        if inspector.use_mock:
            if inspector._is_slurm_available():
                raise ClusterAIError(
                    "En el clúster las corridas cortas se envían a Slurm: no se ejecutan scripts en el nodo "
                    "cabecera. Quite --mock (y CLUSTER_AI_MODE=mock) para usar la corrida corta real."
                )
            # Desarrollo local sin Slurm: ejecución local con telemetría psutil (emulador)
            self.console.console.print(f"[bold cyan]Corrida corta local (emulador) de {format_slurm_time(duration_sec)} para:[/bold cyan] {path.name}")
            job_id = inspector.submit_job(str(path), run_locally=True, timeout_seconds=duration_sec)
            self.console.console.print(f"[bold green][OK] Corrida corta finalizada (emulador). Job ID:[/bold green] {job_id}")
            eff_data = inspector.get_job_efficiency(job_id)
            if eff_data:
                self.console.console.print(f"[dim]Eficiencia de CPU: {eff_data.get('cpu_efficiency_pct', 'N/A')}% | Memoria utilizada: {eff_data.get('memory_utilized', 'N/A')}[/dim]")
            self.console.print_footer()
            return job_id

        partition = self.sbatch_linter.policy.smallest_fitting(duration_sec)
        if partition is None:
            raise ClusterAIError(f"La duración {duration_str} supera el límite de todas las colas: no es una corrida corta.")
        time_limit = format_slurm_time(duration_sec)
        extra_args = ["--time", time_limit, "--partition", partition, "--job-name", f"{path.stem}-shortrun"]

        self.console.console.print(
            f"[bold cyan]Enviando corrida corta a Slurm:[/bold cyan] {path.name} "
            f"(cola [bold]{partition}[/bold], límite [bold]{time_limit}[/bold]; los recursos son los del script)"
        )
        if wait:
            self.console.console.print("[dim]Esperando a que el trabajo finalice (Ctrl+C deja de esperar; el job sigue en la cola)...[/dim]")
        job_id = inspector.submit_job(str(path), run_type=RunType.SHORT_RUN, extra_args=extra_args, wait=wait)
        self.console.console.print(f"[bold green][OK] Corrida corta enviada. Job ID:[/bold green] {job_id}")

        if wait:
            job = self._sync_with_retry(job_id)
            if job is not None:
                self.console.console.print(
                    f"[bold]Telemetría real:[/bold] walltime {format_slurm_time(int(job.walltime_seconds))} | "
                    f"eficiencia CPU {job.cpu_efficiency_pct:.1f}% | memoria pico {format_bytes(job.max_rss_bytes)} | "
                    f"{job.cores} CPUs en {job.nodes} nodo(s)"
                )
                self.console.console.print("[dim]Si el trabajo llegó al límite de tiempo (TIMEOUT), es lo esperado en una corrida corta.[/dim]")
        else:
            self.console.console.print(f"[dim]Seguimiento: squeue -j {job_id}   |   Al finalizar: cluster-ai sync {job_id}[/dim]")

        self.console.print_footer()
        return job_id

    def _sync_with_retry(self, job_id: int, attempts: int = 4, delay: float = 3.0) -> Optional[JobRun]:
        """Importa la telemetría de un job recién terminado (sacct puede tardar unos segundos)."""
        for attempt in range(attempts):
            try:
                return self.sync_job(job_id)
            except ClusterAIError as exc:
                if attempt == attempts - 1:
                    self.console.console.print(f"[yellow]No se pudo importar la telemetría todavía: {exc}[/yellow]")
                    self.console.console.print(f"[dim]Reintente luego con: cluster-ai sync {job_id}[/dim]")
                    return None
                time.sleep(delay)
        return None

    def run_script(self, script_path: str, run_type: RunType = RunType.BASELINE, force_mock: bool = False) -> int:
        """Envía un script para ejecución regular en el clúster (o mockup)."""
        path = Path(script_path).resolve()
        if not path.is_file():
            raise ClusterAIError(f"Archivo de script no encontrado: {script_path}")

        inspector = SlurmInspector(force_mock=force_mock)
        job_id = inspector.submit_job(str(path), run_locally=False)
        self.console.console.print(f"[bold green][OK] Trabajo enviado con exito a la cola de Slurm. Job ID:[/bold green] {job_id}")
        self.console.print_footer()
        return job_id

    def sync_job(self, job_id: int, force_mock: bool = False) -> JobRun:
        """
        Sincroniza la telemetría de un trabajo desde la base de datos de Slurm hacia cluster.db.
        En el servidor la consulta a sacct y la escritura las realiza el helper, que además
        verifica que el job pertenezca al investigador.
        """
        backend = self._backend(force_mock)
        if backend is not None:
            return backend.sync_job(job_id)

        inspector = SlurmInspector(force_mock=force_mock)
        job_run = inspector.get_job_accounting(job_id)
        if not job_run:
            raise ClusterAIError(
                f"No se encontró información contable para el Job ID {job_id} en Slurm. "
                f"Asegúrese de que el trabajo haya finalizado en el clúster o que el ID sea correcto."
            )
        storage = self._get_storage(force_mock=force_mock)
        storage.save_run(job_run)
        return job_run

    def compare(self, baseline_id: int, optimized_id: int, force_mock: bool = False) -> BenchmarkComparison:
        """Compara empíricamente el rendimiento de dos ejecuciones."""
        backend = self._backend(force_mock)
        if backend is not None:
            self.console.console.print("[dim cyan]Consultando telemetría en el servicio Cluster-AI...[/dim cyan]")
            comparison = backend.compare(baseline_id, optimized_id)
            self.console.render_comparison(comparison)
            return comparison

        storage = self._get_storage(force_mock=force_mock)

        # Sincronización automática si no se encuentran en la base de datos local
        if not storage.get_run(baseline_id):
            self.console.console.print(f"[dim cyan]Consultando telemetría del Job {baseline_id} en Slurm...[/dim cyan]")
            self.sync_job(baseline_id, force_mock=force_mock)

        if not storage.get_run(optimized_id):
            self.console.console.print(f"[dim cyan]Consultando telemetría del Job {optimized_id} en Slurm...[/dim cyan]")
            self.sync_job(optimized_id, force_mock=force_mock)

        storage.label_comparison(baseline_id, optimized_id)
        comparison = storage.compare_runs(baseline_id, optimized_id)
        self.console.render_comparison(comparison)
        return comparison

    def history(self, limit: int = 20, user: Optional[str] = None, force_mock: bool = False) -> None:
        """Visualiza el historial de corridas registradas en cluster.db o cluster_mock.db."""
        backend = self._backend(force_mock)
        runs: List[JobRun]
        if backend is not None:
            runs = backend.history(limit=limit, user=user)
        else:
            storage = self._get_storage(force_mock=force_mock)
            runs = storage.list_runs(limit=limit, user=user)
        from rich.table import Table
        db_label = "Cluster AI DB" if not force_mock else "Cluster AI DB (Mock)"
        table = Table(title=f"Historial de Corridas y Trazabilidad de Recursos ({db_label})", header_style="bold magenta")
        table.add_column("JobID", style="dim")
        table.add_column("Usuario")
        table.add_column("Script", style="cyan")
        table.add_column("Tipo", style="bold")
        table.add_column("Cola")
        table.add_column("Walltime", justify="right")
        table.add_column("CPU Eff (%)", justify="right")
        table.add_column("MaxRSS", justify="right")
        table.add_column("Horas-Core", justify="right")

        for r in runs:
            table.add_row(
                str(r.job_id),
                r.user,
                r.script_name,
                r.run_type.value,
                r.partition,
                f"{r.walltime_seconds:.1f}s",
                f"{r.cpu_efficiency_pct:.1f}%",
                f"{round(r.max_rss_bytes / (1024*1024), 1)} MB",
                f"{r.core_hours:.4f}h"
            )

        self.console.console.print(table)
        self.console.print_footer()
