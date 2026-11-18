"""modules/cli/main.py - Punto de entrada de la aplicación CLI de Cluster-AI."""

import sys
from typing import Optional
from dotenv import load_dotenv

# Debe ejecutarse antes de importar `commands`, que instancia el motor al importarse.
# Solo busca el .env de desarrollo junto al código del proyecto (nunca en el directorio
# actual). En el servidor no existe tal archivo: la API Key vive en la configuración
# del helper privilegiado, inaccesible para los investigadores.
load_dotenv()

import typer
from modules.cli import commands
from shared.utils import AUTHOR_INFO

PRODUCTION = commands.PRODUCTION


def _usage(args: str, mock_flag: bool = True) -> str:
    """Sinopsis de argumentos: en producción no se muestra la opción --mock."""
    return f"{args} [--mock]" if (mock_flag and not PRODUCTION) else args

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

app = typer.Typer(
    name="cluster-ai",
    help=(
        "Cluster-AI: Asistente modular de diagnóstico, trazabilidad y optimización para Slurm HPC.\n\n"
        "Diseñado para el clúster de cómputo CLUSTER GAMA (FCAG-IALP)."
    ),
    epilog=f"\n{AUTHOR_INFO}",
    add_completion=False,
    no_args_is_help=True
)

@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        help="Muestra la versión de Cluster-AI y créditos de autoría.",
        callback=commands.version_callback,
        is_eager=True,
    ),
):
    """
    Cluster-AI - Asistente HPC Inteligente (IALP).
    """
    pass

# Registro de subcomandos con parámetros esperados y descripción
app.command(
    name="diagnose",
    help=f"{_usage('<script>')} │ Optimiza las directivas #SBATCH: crea respaldo inmutable, versión optimizada e informe Markdown."
)(commands.diagnose)

app.command(
    name="run",
    help=f"{_usage('<script>')} │ Envía un script a la cola de Slurm" + ("." if PRODUCTION else " (o al emulador Slurm-Mockup).")
)(commands.run)

app.command(
    name="short-run",
    help="<script> [-d HH:MM:SS] [--wait] │ Corrida corta en Slurm, acotada en tiempo, para medir CPU y memoria antes de una corrida larga."
)(commands.short_run)

app.command(
    name="compare",
    help=f"{_usage('<baseline_job> <optimized_job>')} │ Compara el consumo de recursos entre baseline y optimizado en Cluster AI DB."
)(commands.compare)

app.command(
    name="sync",
    help=f"{_usage('<job_id>')} │ Importa y sincroniza la telemetría de un Job finalizado desde Slurm hacia Cluster AI DB."
)(commands.sync)

app.command(
    name="history",
    help=f"{_usage('[--limit N] [--user U]')} │ Lista el historial de trabajos y telemetría registrada en Cluster AI DB."
)(commands.history)

# El emulador solo se ofrece fuera del servidor de producción
if not PRODUCTION:
    app.command(
        name="mock",
        help="<sinfo|squeue|submit|seff> (script/id) │ Inspección y prueba directa contra el emulador Slurm-Mockup."
    )(commands.mock)

if __name__ == "__main__":
    app()
