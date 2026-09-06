"""modules/engine/runner.py - Motor central de orquestación del pipeline de Cluster-AI."""

from pathlib import Path
from typing import Optional
from modules.engine.ai import AgnesAIClient
from modules.engine.context import ContextAssembler
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
from shared.models import AnalysisResult, BenchmarkComparison, JobRun, RunType
from shared.utils import compute_sha256, parse_slurm_time

class EngineRunner:
    """Orquesta inspectores, linters, inteligencia artificial, almacenamiento y reporteros."""

    def __init__(
        self,
        storage: Optional[StorageEngine] = None,
        ai_client: Optional[AgnesAIClient] = None,
        console_reporter: Optional[ConsoleReporter] = None,
        markdown_reporter: Optional[MarkdownReporter] = None
    ):
        self._custom_storage = storage
        self.ai_client = ai_client or AgnesAIClient()
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

    def diagnose(self, script_path: str, force_mock: bool = False) -> AnalysisResult:
        """
        Ejecuta el ciclo de vida completo de diagnóstico:
        1. Respaldo inmutable del script original (.bak_<timestamp>)
        2. Análisis determinístico con linters (sbatch, python, mpi)
        3. Generación de versión optimizada (.optimized.sh)
        4. Auditoría profunda con Agnes AI (agnes-2.5-flash)
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

        # Buscar archivos de código asociados (ej. Python) y auditarlos
        invoked_files = self.context_assembler.find_invoked_code_files(path, original_content)
        code_suggestions = []
        for code_file in invoked_files:
            try:
                code_text = safe_read_file(code_file)
                code_issues = self.python_linter.lint(str(code_file), code_text)
                issues.extend(code_issues)
                suggestions = self.python_linter.extract_code_suggestions(str(code_file), code_text)
                code_suggestions.extend(suggestions)
            except Exception:
                pass

        # Determinar partición recomendada
        recommended_part = "medium"
        for l in optimized_content.splitlines():
            if "-p " in l:
                recommended_part = l.split("-p ")[1].split()[0]
                break

        # 3. Consulta a Agnes AI para análisis avanzado
        issues_summary = "\n".join([f"- [{i.severity.value}] {i.rule_id}: {i.message}" for i in issues])
        ai_messages = self.context_assembler.assemble_ai_prompt(
            path,
            original_content,
            issues_summary=issues_summary
        )

        with self.console.console.status("[bold cyan]Consultando a Agnes AI (agnes-2.5-flash) para optimización de simulación...[/bold cyan]"):
            ai_insights = self.ai_client.chat_completion(ai_messages, temperature=0.6, max_tokens=2048)

        # 4. Consolidar resultado
        result = AnalysisResult(
            script_path=str(path),
            script_hash=script_hash,
            backup_path=str(backup_path),
            optimized_script_path=str(optimized_path),
            issues=issues,
            code_suggestions=code_suggestions,
            recommended_partition=recommended_part
        )

        # 5. Generar informe técnico en Markdown
        report_path = self.markdown.generate_report(result, ai_insights=ai_insights, sbatch_diff=sbatch_diff)
        result.report_markdown_path = str(report_path)

        # 6. Persistir en cluster.db o cluster_mock.db según modo
        storage = self._get_storage(force_mock=force_mock)
        storage.save_analysis(result, markdown_content=ai_insights)

        # 7. Renderizar en consola
        self.console.render_analysis(result)
        return result

    def short_run(self, script_path: str, duration_str: str = "00:10:00", force_mock: bool = True) -> int:
        """
        Ejecuta una corrida corta de prueba acotada en tiempo para scripts de larga duración.
        Permite medir la tasa de consumo de CPU y memoria sin esperar semanas o meses.
        """
        path = Path(script_path).resolve()
        if not path.is_file():
            raise ClusterAIError(f"Archivo de script no encontrado: {script_path}")

        duration_sec = parse_slurm_time(duration_str) or 600
        inspector = SlurmInspector(force_mock=force_mock)

        self.console.console.print(f"[bold cyan]Iniciando corrida corta diagnóstica de prueba ({duration_str}) para:[/bold cyan] {path.name}")
        job_id = inspector.submit_job(str(path), run_locally=True, timeout_seconds=duration_sec)

        self.console.console.print(f"[bold green][OK] Corrida corta finalizada. Job ID registrado:[/bold green] {job_id}")
        eff_data = inspector.get_job_efficiency(job_id)
        if eff_data:
            self.console.console.print(f"[dim]Eficiencia de CPU: {eff_data.get('cpu_efficiency_pct', 'N/A')}% | Memoria utilizada: {eff_data.get('memory_utilized', 'N/A')}[/dim]")

        self.console.print_footer()
        return job_id

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

    def compare(self, baseline_id: int, optimized_id: int, force_mock: bool = False) -> BenchmarkComparison:
        """Compara empíricamente el rendimiento de dos ejecuciones."""
        storage = self._get_storage(force_mock=force_mock)
        comparison = storage.compare_runs(baseline_id, optimized_id)
        self.console.render_comparison(comparison)
        return comparison

    def history(self, limit: int = 20, user: Optional[str] = None, force_mock: bool = False) -> None:
        """Visualiza el historial de corridas registradas en cluster.db o cluster_mock.db."""
        storage = self._get_storage(force_mock=force_mock)
        runs = storage.list_runs(limit=limit, user=user)
        from rich.table import Table
        db_label = storage.db_path.name
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
