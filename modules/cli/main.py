"""modules/cli/main.py - Punto de entrada de la aplicación CLI de Cluster-AI."""

import sys
from typing import Optional
from dotenv import load_dotenv
import typer
from modules.cli import commands
from shared.utils import AUTHOR_INFO

load_dotenv()

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

# Registro de subcomandos
app.command(name="diagnose", help="Diagnostica un script Slurm, crea respaldo inmutable, genera versión optimizada e informe Markdown.")(commands.diagnose)
app.command(name="run", help="Envía un script al gestor de colas de Slurm (o mockup) y registra telemetría.")(commands.run)
app.command(name="short-run", help="Ejecuta una corrida corta de prueba acotada en tiempo para simulaciones extensas.")(commands.short_run)
app.command(name="compare", help="Compara el consumo de recursos entre una corrida original (baseline) y una optimizada.")(commands.compare)
app.command(name="history", help="Lista el historial de trabajos evaluados y telemetría registrada en cluster.db.")(commands.history)
app.command(name="mock", help="Comandos de inspección y prueba directa contra el emulador Slurm-Mockup.")(commands.mock)

if __name__ == "__main__":
    app()
