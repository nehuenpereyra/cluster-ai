"""modules/cli/commands.py - Subcomandos expuestos en la interfaz de consola de Cluster-AI."""

from typing import Optional
import typer
from modules.engine.runner import EngineRunner
from modules.mock.simulator import SlurmSimulator
from shared.exceptions import ClusterAIError
from shared.features import is_production
from shared.models import RunType
from shared.utils import AUTHOR_INFO

# En producción (servidor del clúster) el emulador Slurm-Mockup no se ofrece: el comando
# `mock` no se registra y las opciones --mock quedan ocultas y rechazadas.
PRODUCTION = is_production()

runner = EngineRunner()


def _reject_mock_in_production(mock: Optional[bool]) -> None:
    if mock and PRODUCTION:
        from rich import print
        print("[bold red]El modo --mock (emulador Slurm-Mockup) no está disponible en el servidor de producción.[/bold red]")
        raise typer.Exit(code=2)

def version_callback(value: bool):
    if value:
        from rich import print
        print("[bold cyan]Cluster-AI[/bold cyan] versión [bold green]0.0.1[/bold green] (HPC Optimization Assistant)")
        print(f"\n[dim]{AUTHOR_INFO}[/dim]")
        raise typer.Exit()

def diagnose(
    script: str = typer.Argument(..., help="Ruta al script Slurm (.sh o .sbatch) a diagnosticar"),
    mock: bool = typer.Option(False, "--mock", help="Forzar modo Slurm-Mockup para pruebas locales", hidden=PRODUCTION)
):
    """
    Diagnostica y optimiza las directivas de un script Slurm (#SBATCH).
    Crea una copia de respaldo inmutable (.bak_<timestamp>), genera una versión optimizada,
    consulta al Asistente IA sobre recursos y directivas y emite un informe técnico en Markdown.
    (La auditoría del código fuente del proyecto no está habilitada en esta fase.)
    """
    _reject_mock_in_production(mock)
    try:
        runner.diagnose(script, force_mock=mock)
    except ClusterAIError as e:
        from rich import print
        print(f"[bold red]Error en el diagnóstico:[/bold red] {e}")
        raise typer.Exit(code=1)

def run(
    script: str = typer.Argument(..., help="Ruta al script Slurm a enviar a la cola"),
    mock: bool = typer.Option(False, "--mock", help="Forzar envío a través de Slurm-Mockup", hidden=PRODUCTION)
):
    """
    Envía un trabajo a la cola de Slurm.
    """
    _reject_mock_in_production(mock)
    try:
        runner.run_script(script, run_type=RunType.BASELINE, force_mock=mock)
    except ClusterAIError as e:
        from rich import print
        print(f"[bold red]Error al enviar el trabajo:[/bold red] {e}")
        raise typer.Exit(code=1)

def short_run(
    script: str = typer.Argument(..., help="Ruta al script Slurm de simulación"),
    duration: str = typer.Option("00:10:00", "--duration", "-d", help="Tiempo límite de la corrida corta (HH:MM:SS; un número solo son minutos)"),
    wait: bool = typer.Option(False, "--wait", help="Esperar a que termine y mostrar su telemetría real"),
    mock: Optional[bool] = typer.Option(None, "--mock/--real", help="Solo desarrollo: --mock usa el emulador local (no disponible en el clúster)", hidden=PRODUCTION)
):
    """
    Envía una corrida corta a Slurm (misma configuración del script, con tiempo acotado) para
    medir el consumo de CPU y memoria antes de enviar simulaciones que tardarían semanas o meses.
    """
    _reject_mock_in_production(mock)
    try:
        runner.short_run(script, duration_str=duration, force_mock=mock, wait=wait)
    except ClusterAIError as e:
        from rich import print
        print(f"[bold red]Error en la corrida corta:[/bold red] {e}")
        raise typer.Exit(code=1)

def compare(
    baseline_job: int = typer.Argument(..., help="Job ID de la ejecución original (baseline)"),
    optimized_job: int = typer.Argument(..., help="Job ID de la ejecución optimizada"),
    mock: bool = typer.Option(False, "--mock", help="Consultar base de datos de pruebas (cluster_mock.db)", hidden=PRODUCTION)
):
    """
    Compara empíricamente los recursos consumidos entre la corrida baseline y la optimizada,
    calculando Speedup, ahorro de memoria, ganancia de eficiencia y horas-núcleo salvadas.
    """
    _reject_mock_in_production(mock)
    try:
        runner.compare(baseline_job, optimized_job, force_mock=mock)
    except Exception as e:
        from rich import print
        print(f"[bold red]Error en comparativa:[/bold red] {e}")
        db_label = "Cluster AI DB (Mock)" if mock else "Cluster AI DB"
        print(f"[dim]Sugerencia: Ejecute 'cluster-ai history{' --mock' if mock else ''}' para verificar los Job IDs registrados en {db_label}.[/dim]")
        raise typer.Exit(code=1)

def sync(
    job_id: int = typer.Argument(..., help="Job ID de Slurm a sincronizar con Cluster AI DB"),
    mock: bool = typer.Option(False, "--mock", help="Sincronizar desde la emulación local", hidden=PRODUCTION)
):
    """
    Importa y sincroniza la telemetría de un Job finalizado desde la base de datos de Slurm hacia Cluster AI DB.
    """
    _reject_mock_in_production(mock)
    try:
        job = runner.sync_job(job_id, force_mock=mock)
        from rich import print
        db_label = "Cluster AI DB (Mock)" if mock else "Cluster AI DB"
        print(f"[bold green][OK] Job {job_id} sincronizado exitosamente en {db_label}:[/bold green]")
        print(f"  • Script: [cyan]{job.script_name}[/cyan] ({job.run_type.value})")
        print(f"  • Partición: {job.partition} | Cores: {job.cores} | Walltime: {job.walltime_seconds:.1f}s")
        print(f"  • Eficiencia CPU: {job.cpu_efficiency_pct:.1f}% | MaxRSS: {round(job.max_rss_bytes / (1024*1024), 2)} MB")
        print(f"  • Horas-Núcleo: {job.core_hours:.4f}h")
    except Exception as e:
        from rich import print
        print(f"[bold red]Error al sincronizar Job {job_id}:[/bold red] {e}")
        raise typer.Exit(code=1)

def history(
    limit: int = typer.Option(20, "--limit", "-n", help="Cantidad máxima de corridas a mostrar"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Filtrar corridas por usuario"),
    mock: bool = typer.Option(False, "--mock", help="Mostrar historial de pruebas (Cluster AI DB Mock)", hidden=PRODUCTION)
):
    """
    Muestra el historial de corridas y trazabilidad de recursos registradas en Cluster AI DB.
    """
    _reject_mock_in_production(mock)
    try:
        runner.history(limit=limit, user=user, force_mock=mock)
    except ClusterAIError as e:
        from rich import print
        print(f"[bold red]Error al consultar el historial:[/bold red] {e}")
        raise typer.Exit(code=1)

def mock(
    action: str = typer.Argument("sinfo", help="Acción de emulación: 'sinfo', 'squeue', 'submit', 'seff'"),
    target: Optional[str] = typer.Argument(None, help="Parámetro complementario (script a enviar o job_id a consultar)")
):
    """
    Control directo del módulo de emulación Slurm-Mockup para pruebas y dry-runs.
    """
    from rich import print
    from rich.table import Table
    sim = SlurmSimulator()

    if action == "sinfo":
        info = sim.sinfo()
        table = Table(title="Slurm-Mockup: Estado de Nodos y Particiones IALP", header_style="bold cyan")
        table.add_column("Partición", style="bold")
        table.add_column("Estado")
        table.add_column("Límite Tiempo")
        table.add_column("Nodos")
        table.add_column("Lista de Nodos")
        table.add_column("Predeterminada")

        for p in info:
            is_def = "[bold green]Si[/bold green]" if p["is_default"] else "No"
            table.add_row(p["partition"], p["avail"], p["timelimit"], str(p["nodes"]), p["nodelist"], is_def)
        print(table)

    elif action == "squeue":
        q = sim.squeue()
        table = Table(title="Slurm-Mockup: Cola de Trabajos Activos", header_style="bold green")
        table.add_column("JobID")
        table.add_column("Partición")
        table.add_column("Nombre")
        table.add_column("Usuario")
        table.add_column("Estado")
        table.add_column("Cores")

        if q:
            for job in q:
                table.add_row(str(job["job_id"]), job["partition"], job["job_name"], job["user"], job["status"], str(job["cores"]))
        else:
            table.add_row("-", "-", "[dim]No hay trabajos en cola[/dim]", "-", "-", "-")
        print(table)

    elif action == "submit":
        if not target:
            print("[bold red]Error:[/bold red] Debe indicar la ruta del script a enviar. Ej: cluster-ai mock submit script.sh")
            raise typer.Exit(code=1)
        job_id = sim.sbatch(target, run_locally=False)
        print(f"[bold green][OK] Trabajo simulado enviado a Slurm-Mockup. Job ID:[/bold green] {job_id}")

    elif action == "seff":
        if not target or not target.isdigit():
            print("[bold red]Error:[/bold red] Debe indicar un Job ID numérico. Ej: cluster-ai mock seff 1")
            raise typer.Exit(code=1)
        eff = sim.seff(int(target))
        print(f"[bold cyan]Eficiencia de Trabajo {target}:[/bold cyan] CPU: {eff['cpu_efficiency_pct']}% | Memoria: {eff['memory_utilized']} | Walltime: {eff['walltime']}")

    else:
        print(f"[bold yellow]Acción desconocida '{action}'. Opciones: sinfo, squeue, submit, seff[/bold yellow]")

    print(f"\n[dim]{AUTHOR_INFO}[/dim]")
