"""shared/utils.py - Funciones puras y utilidades reutilizables."""

import hashlib
import re
from typing import Union

AUTHOR_INFO = "Autor: Ing. Nehuen Pereyra (CPA IALP)"


def get_author_footer() -> str:
    """Devuelve la leyenda oficial del autor para pies de página y outputs."""
    return f"\n[dim]{AUTHOR_INFO}[/dim]"


def compute_sha256(content: Union[str, bytes]) -> str:
    """Calcula el hash SHA256 de una cadena de texto o bytes."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def parse_slurm_time(time_str: str) -> int:
    """
    Convierte una cadena de tiempo en formato Slurm a segundos totales.
    Formatos admitidos:
      - 'MM:SS'
      - 'HH:MM:SS'
      - 'D-HH:MM:SS'
      - 'D-HH:MM'
    """
    if not time_str or not isinstance(time_str, str):
        return 0
    time_str = time_str.strip()

    # Días presentes (ej. 1-12:00:00)
    days = 0
    if "-" in time_str:
        day_part, time_str = time_str.split("-", 1)
        try:
            days = int(day_part)
        except ValueError:
            days = 0

    parts = time_str.split(":")
    hours, minutes, seconds = 0, 0, 0
    try:
        if len(parts) == 3:
            hours, minutes, seconds = int(
                parts[0]), int(parts[1]), int(parts[2])
        elif len(parts) == 2:
            minutes, seconds = int(parts[0]), int(parts[1])
        elif len(parts) == 1:
            seconds = int(parts[0])
    except ValueError:
        pass

    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def format_slurm_time(seconds: int) -> str:
    """Convierte segundos a formato estándar Slurm (D-HH:MM:SS o HH:MM:SS)."""
    if seconds < 0:
        return "00:00:00"
    days = seconds // 86400
    remainder = seconds % 86400
    hours = remainder // 3600
    remainder = remainder % 3600
    minutes = remainder // 60
    secs = remainder % 60

    if days > 0:
        return f"{days}-{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_bytes(bytes_val: Union[int, float]) -> str:
    """Convierte un valor en bytes a formato legible (KB, MB, GB, TB)."""
    if bytes_val is None or bytes_val <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_idx = 0
    val = float(bytes_val)
    while val >= 1024.0 and unit_idx < len(units) - 1:
        val /= 1024.0
        unit_idx += 1
    return f"{val:.2f} {units[unit_idx]}"


def calculate_core_hours(cores: int, walltime_seconds: float) -> float:
    """Calcula las horas-núcleo consumidas."""
    return round((cores * max(walltime_seconds, 0.0)) / 3600.0, 4)


def calculate_speedup(time_baseline: float, time_optimized: float) -> float:
    """Calcula el factor de aceleración Speedup (T_base / T_opt)."""
    if time_optimized <= 0:
        return 1.0
    return round(time_baseline / time_optimized, 2)
