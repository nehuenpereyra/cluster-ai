"""modules/reporters/base.py - Interfaz abstracta para reporteros de resultados."""

from abc import ABC, abstractmethod
from typing import Any
from shared.models import AnalysisResult, BenchmarkComparison

class BaseReporter(ABC):
    """Clase base para la presentación de resultados."""

    @abstractmethod
    def render_analysis(self, result: AnalysisResult) -> None:
        """Renderiza los resultados del diagnóstico."""
        pass

    @abstractmethod
    def render_comparison(self, comparison: BenchmarkComparison) -> None:
        """Renderiza la comparación entre dos ejecuciones."""
        pass
