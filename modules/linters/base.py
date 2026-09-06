"""modules/linters/base.py - Interfaz abstracta para linters determinísticos."""

from abc import ABC, abstractmethod
from typing import List, Tuple
from shared.models import LintIssue

class BaseLinter(ABC):
    """Clase base para todos los validadores de scripts y código."""

    @abstractmethod
    def can_handle(self, file_path: str, content: str) -> bool:
        """Determina si este linter aplica para el archivo o contenido provisto."""
        pass

    @abstractmethod
    def lint(self, file_path: str, content: str) -> List[LintIssue]:
        """Ejecuta las validaciones determinísticas y devuelve los problemas detectados."""
        pass

    @abstractmethod
    def fix(self, file_path: str, content: str) -> Tuple[str, List[str]]:
        """
        Aplica correcciones determinísticas sugeridas.
        Devuelve (nuevo_contenido, lista_de_cambios_realizados).
        """
        pass
