"""Punto de entrada: `python -I -m modules.privileged <subcomando> [args]` (invocado por sudo)."""

import os
import sys

from modules.privileged.helper import main

if __name__ == "__main__":
    # Entorno neutro: el directorio actual (la carpeta del investigador) no debe influir
    # en nada, y todo archivo nuevo (base SQLite y auxiliares WAL) nace privado.
    os.chdir("/")
    os.umask(0o077)
    sys.exit(main())
