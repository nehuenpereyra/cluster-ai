"""modules/cli/commands.py - Subcomandos expuestos en la interfaz de consola de Cluster-AI."""

from typing import Optional
import typer
from modules.engine.runner import EngineRunner
from modules.mock.simulator import SlurmSimulator
from shared.models import RunType
from shared.utils import AUTHOR_INFO

runner = EngineRunner()

def version_callback(value: bool):
    if value:
        from rich import print
        print("[bold cyan]Cluster-AI[/bold cyan] versión [bold green]0.0.1[/bold green] (HPC Optimization Assistant)")
        print(f"\n[dim]{AUTHOR_INFO}[/dim]")
        raise typer.Exit()

def diagnose(
    script: str = typer.Argument(..., help="Ruta al script Slurm (.sh o .sbatch) a diagnosticar"),
    mock: bool = typer.Option(False, "--mock", help="Forzar modo Slurm-Mockup para pruebas locales")
):
    """
    Diagnostica un script Slurm (#SBATCH) y su código de simulación asociado.
    Crea una copia de respaldo inmutable (.bak_<timestamp>), genera una versión optimizada,
    audita cuellos de botella con Agnes AI y emite un informe técnico en Markdown.
    """
    runner.diagnose(script, force_mock=mock)

def run(
    script: str = typer.Argument(..., help="Ruta al script Slurm a enviar a la cola"),
    mock: bool = typer.Option(False, "--mock", help="Forzar envío a través de Slurm-Mockup")
):
    """
    Envía un trabajo a la cola de Slurm (o mockup) y monitorea el registro de telemetría.
    """
    runner.run_script(script, run_type=RunType.BASELINE, force_mock=mock)

def short_run(
    script: str = typer.Argument(..., help="Ruta al script Slurm de simulación"),
    duration: str = typer.Option("00:10:00", "--duration", "-d", help="Tiempo límite para la corrida corta (formato HH:MM:SS)"),
    mock: bool = typer.Option(True, "--mock/--real", help="Usar emulador local con monitoreo de procesos (por defecto)")
):
    """
    Ejecuta una corrida corta diagnóstica para medir la tasa de consumo de CPU y memoria
    antes de enviar simulaciones que tardarían semanas o meses.
    """
    runner.short_run(script, duration_str=duration, force_mock=mock)

def compare(
    baseline_job: int = typer.Argument(..., help="Job ID de la ejecución original (baseline)"),
    optimized_job: int = typer.Argument(..., help="Job ID de la ejecución optimizada"),
    mock: bool = typer.Option(False, "--mock", help="Consultar base de datos de pruebas (cluster_mock.db)")
):
    """
    Compara empíricamente los recursos consumidos entre la corrida baseline y la optimizada,
    calculando Speedup, ahorro de memoria, ganancia de eficiencia y horas-núcleo salvadas.
    """
    try:
        runner.compare(baseline_job, optimized_job, force_mock=mock)
    except Exception as e:
        from rich import print
        print(f"[bold red]Error en comparativa:[/bold red] {e}")
        db_name = "cluster_mock.db" if mock else "cluster.db"
        print(f"[dim]Sugerencia: Ejecute 'cluster-ai history{' --mock' if mock else ''}' para verificar los Job IDs registrados en {db_name}.[/dim]")
        raise typer.Exit(code=1)

def history(
    limit: int = typer.Option(20, "--limit", "-n", help="Cantidad máxima de corridas a mostrar"),
    user: Optional[str] = typer.Option(None, "--user", "-u", help="Filtrar corridas por usuario"),
    mock: bool = typer.Option(False, "--mock", help="Mostrar historial de pruebas (cluster_mock.db)")
):
    """
    Muestra el historial de corridas y trazabilidad de recursos registradas en cluster.db.
    """
    runner.history(limit=limit, user=user, force_mock=mock)

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
