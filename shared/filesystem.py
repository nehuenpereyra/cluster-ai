"""shared/filesystem.py - Gestión segura de archivos, copias de respaldo inmutables y diffs."""

import difflib
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Tuple, Union
from shared.exceptions import BackupError

def safe_read_file(file_path: Union[str, Path]) -> str:
    """Lee el contenido de un archivo de texto de forma segura."""
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"No se encontró el archivo: {path}")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()

def create_immutable_backup(script_path: Union[str, Path]) -> Path:
    """
    Crea una copia de respaldo inmutable con timestamp del script original.
    Garantiza que el archivo original NUNCA sea borrado ni sobrescrito.
    Formato: <script>.sh.bak_<YYYYMMDD_HHMMSS>
    """
    path = Path(script_path).resolve()
    if not path.is_file():
        raise BackupError(f"No se puede respaldar un archivo inexistente: {path}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.parent / f"{path.name}.bak_{timestamp}"
    
    try:
        shutil.copy2(path, backup_path)
        # Permisos de solo lectura para preservar inmutabilidad
        try:
            os.chmod(backup_path, 0o444)
        except Exception:
            pass  # En Windows el modo de solo lectura puede manejarse con atributos si es necesario
        return backup_path
    except Exception as e:
        raise BackupError(f"Error al generar la copia de respaldo inmutable: {e}") from e

def write_optimized_script(original_path: Union[str, Path], optimized_content: str) -> Path:
    """
    Guarda la versión optimizada generada en un archivo complementario (.optimized.sh).
    Preserva intacto el archivo original del investigador.
    """
    path = Path(original_path).resolve()
    suffix = path.suffix if path.suffix else ".sh"
    stem = path.stem
    optimized_path = path.parent / f"{stem}.optimized{suffix}"
    
    with open(optimized_path, "w", encoding="utf-8") as f:
        f.write(optimized_content)
        
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
