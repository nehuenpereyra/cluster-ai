"""shared/filesystem.py - Lectura y escritura segura de archivos, versiones previas y diffs."""

import difflib
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Union
from shared.exceptions import BackupError

def safe_read_file(file_path: Union[str, Path]) -> str:
    """Lee el contenido de un archivo de texto de forma segura."""
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"No se encontró el archivo: {path}")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()

def optimized_script_path(original_path: Union[str, Path]) -> Path:
    """Ruta de la versión optimizada de un script: `<script>.optimized.<ext>`."""
    path = Path(original_path).resolve()
    return path.parent / f"{path.stem}.optimized{path.suffix or '.sh'}"

def _previous_version_path(target: Path) -> Path:
    """`<nombre>_<fecha de la versión anterior><ext>`, sin pisar ninguna versión ya guardada."""
    stamp = datetime.fromtimestamp(target.stat().st_mtime).strftime("%Y%m%d_%H%M%S")
    candidate = target.with_name(f"{target.stem}_{stamp}{target.suffix}")
    n = 2
    while candidate.exists():
        candidate = target.with_name(f"{target.stem}_{stamp}_{n}{target.suffix}")
        n += 1
    return candidate

def write_preserving_previous(target: Union[str, Path], content: str) -> Optional[Path]:
    """
    Escribe `content` en `target`. Si ya existía con otro contenido (por ejemplo, una versión
    optimizada que el investigador editó a mano), la versión anterior se conserva renombrándola
    con la fecha en que fue generada. Devuelve la ruta de esa versión conservada, o None.
    """
    path = Path(target)
    previous: Optional[Path] = None
    try:
        if path.is_file():
            if safe_read_file(path) == content:
                return None
            previous = _previous_version_path(path)
            path.rename(previous)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        raise BackupError(f"No se pudo guardar {path.name} conservando la versión anterior: {e}") from e
    return previous

def write_optimized_script(original_path: Union[str, Path], optimized_content: str) -> Path:
    """
    Guarda la versión optimizada en un archivo complementario (`.optimized.sh`), conservando la
    versión anterior si difiere. El archivo original del investigador nunca se modifica.
    """
    optimized_path = optimized_script_path(original_path)
    write_preserving_previous(optimized_path, optimized_content)
    return optimized_path

def generate_diff(original_text: str, modified_text: str, fromfile: str = "Original", tofile: str = "Optimizado") -> str:
    """Genera un diff unificado entre el texto original y el optimizado."""
    orig_lines = original_text.splitlines(keepends=True)
    mod_lines = modified_text.splitlines(keepends=True)
    
    diff_lines = difflib.unified_diff(
        orig_lines,
        mod_lines,
        fromfile=fromfile,
        tofile=tofile,
        lineterm=""
    )
    return "".join(diff_lines)
