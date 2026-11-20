"""modules/reporters/console.py - Reportero visual en terminal con Rich."""

import sys
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.syntax import Syntax
from modules.reporters.base import BaseReporter
from shared.models import AnalysisResult, BenchmarkComparison, LintSeverity
from shared.utils import AUTHOR_INFO, format_bytes, format_slurm_time

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

class ConsoleReporter(BaseReporter):
    """Genera visualizaciones interactivas, tablas comparativas y diffs con Rich."""

    def __init__(self, console: Console = None):
        self.console = console or Console(safe_box=True)

    def print_footer(self) -> None:
        """Imprime la autoría oficial al final de la salida del comando."""
        self.console.print(f"\n[dim]────────────────────────────────────────────────────────────[/dim]")
        self.console.print(f"[bold dim]{AUTHOR_INFO}[/bold dim]\n")

    def render_analysis(self, result: AnalysisResult) -> None:
        body = (
            f"[bold cyan]Diagnóstico de Simulación:[/bold cyan] [green]{result.script_path}[/green]\n"
            f"[dim]Cola recomendada: [bold yellow]{result.recommended_partition}[/bold yellow] | "
            f"Script optimizado: [italic]{result.optimized_script_path or 'N/A'}[/italic][/dim]"
        )
        if result.previous_versions:
            names = ", ".join(Path(p).name for p in result.previous_versions)
            body += f"\n[dim]Versiones anteriores conservadas: [italic]{names}[/italic][/dim]"
        self.console.print(Panel.fit(body, title="[bold blue]Cluster-AI HPC Diagnóstico[/bold blue]"))

        # Tabla de problemas detectados por Linters
        if result.issues:
            table = Table(title="Problemas Detectados en Script de Envío", show_header=True, header_style="bold magenta")
            table.add_column("Regla", style="dim", width=12)
            table.add_column("Severidad", width=10)
            table.add_column("Mensaje", style="white")
            table.add_column("Sugerencia", style="green")

            for iss in result.issues:
                sev_color = "red" if iss.severity == LintSeverity.ERROR else ("yellow" if iss.severity == LintSeverity.WARNING else "blue")
                table.add_row(
                    iss.rule_id,
                    f"[{sev_color}]{iss.severity.value.upper()}[/{sev_color}]",
                    iss.message,
                    iss.suggestion or ""
                )
            self.console.print(table)
        else:
            self.console.print("[bold green][OK] No se encontraron errores en la configuracion de #SBATCH.[/bold green]\n")

        # Sugerencias de optimizacion de codigo
        if result.code_suggestions:
            self.console.print("\n[bold cyan]Sugerencias de Optimizacion de Codigo Fuente:[/bold cyan]")
            for sug in result.code_suggestions:
                panel_body = (
                    f"[bold yellow]{sug.title}[/bold yellow] ([italic]{sug.category}[/italic])\n\n"
                    f"{sug.explanation}\n\n"
                    f"[bold red]Codigo Original:[/bold red]\n```\n{sug.original_code}\n```\n\n"
                    f"[bold green]Codigo Optimizado:[/bold green]\n```\n{sug.optimized_code}\n```\n\n"
                    f"[bold cyan]Ganancia Estimada:[/bold cyan] {sug.estimated_gain or 'Alta'}"
                )
                self.console.print(Panel(panel_body, border_style="cyan"))

        if result.optimized_script_path:
            self.console.print(f"[bold green][OK] Script optimizado generado en:[/bold green] {result.optimized_script_path}")

        if result.report_markdown_path:
            self.console.print(f"[bold green][OK] Informe Markdown tecnico guardado en:[/bold green] {result.report_markdown_path}")

        self.print_footer()

    def render_comparison(self, comp: BenchmarkComparison) -> None:
        """Renderiza la tabla comparativa de recursos (Baseline vs Optimizado)."""
        table = Table(title="Comparativa Empírica de Rendimiento (Baseline vs Optimizado)", header_style="bold cyan")
        table.add_column("Métrica", style="bold")
        table.add_column("Baseline (Original)", justify="right", style="yellow")
        table.add_column("Optimizado", justify="right", style="green")
        table.add_column("Mejora / Ahorro", justify="right", style="bold green")

        # Cada diferencia se muestra con su signo real: verde si el optimizado mejora, rojo si empeora.
        def delta(saved: float, text: str, better_label: str, worse_label: str) -> str:
            if abs(saved) < 1e-9:
                return "[dim]sin cambios[/dim]"
            if saved > 0:
                return f"[green]-{text} {better_label}[/green]"
            return f"[red]+{text} {worse_label}[/red]"

        # Tiempo de pared
        t_base = format_slurm_time(int(comp.baseline_run.walltime_seconds))
        t_opt = format_slurm_time(int(comp.optimized_run.walltime_seconds))
        time_text = format_slurm_time(int(abs(comp.walltime_saved_seconds)))
        not_applicable = "[yellow]no aplica (corrida acotada)[/yellow]"
        table.add_row(
            "Tiempo de Pared (Walltime)", t_base, t_opt,
            not_applicable if comp.time_limited else
            f"{delta(comp.walltime_saved_seconds, time_text, '', 'más lento')} ({comp.speedup}x Speedup)"
        )

        # Eficiencia de CPU (aquí positivo = mejora)
        eff_base = f"{comp.baseline_run.cpu_efficiency_pct:.1f}%"
        eff_opt = f"{comp.optimized_run.cpu_efficiency_pct:.1f}%"
        gain = comp.cpu_efficiency_gain_pct
        eff_diff = "[dim]sin cambios[/dim]" if abs(gain) < 1e-9 else (
            f"[green]+{gain:.1f}%[/green]" if gain > 0 else f"[red]{gain:.1f}%[/red]")
        table.add_row("Eficiencia de CPU", eff_base, eff_opt, eff_diff)

        # Memoria máxima RSS
        mem_base = format_bytes(comp.baseline_run.max_rss_bytes)
        mem_opt = format_bytes(comp.optimized_run.max_rss_bytes)
        mem_text = format_bytes(abs(comp.memory_saved_bytes))
        table.add_row("Memoria Pico (MaxRSS)", mem_base, mem_opt, delta(comp.memory_saved_bytes, mem_text, "", "más memoria"))

        # Horas-Núcleo
        ch_text = f"{abs(comp.core_hours_saved):.4f} h"
        table.add_row(
            "Horas-Núcleo Consumidas", f"{comp.baseline_run.core_hours:.4f} h", f"{comp.optimized_run.core_hours:.4f} h",
            not_applicable if comp.time_limited else delta(comp.core_hours_saved, ch_text, "ahorradas", "adicionales")
        )

        self.console.print(Panel.fit(table, title="[bold blue]Resultados de Trazabilidad IALP[/bold blue]"))
        if comp.time_limited:
            self.console.print(
                "[yellow]Al menos un trabajo terminó por alcanzar su tiempo límite: el tiempo, el speedup y las "
                "horas-núcleo no son comparables. Sí lo son la eficiencia de CPU y la memoria pico.[/yellow]\n"
                "[dim]Para medir el speedup, ejecute la corrida completa (sin límite de tiempo): "
                "cluster-ai benchmark <script>[/dim]"
            )
        self.print_footer()
