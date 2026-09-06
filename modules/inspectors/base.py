"""modules/inspectors/base.py - Interfaz abstracta para inspectores del clúster."""

from abc import ABC, abstractmethod
from typing import Any, Dict

class BaseInspector(ABC):
    """Clase base para inspección de recursos y telemetría."""

    @abstractmethod
    def inspect(self) -> Dict[str, Any]:
        """Extrae el estado en vivo o estático del componente inspeccionado."""
        pass
