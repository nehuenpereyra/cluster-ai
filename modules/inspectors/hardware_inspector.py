"""modules/inspectors/hardware_inspector.py - Inspector de especificaciones de hardware del clúster."""

from pathlib import Path
from typing import Any, Dict, Optional
import psutil
import yaml
from modules.inspectors.base import BaseInspector

class HardwareInspector(BaseInspector):
    """
    Inspecciona la topología de hardware teórica del clúster IALP
    y el hardware local de la máquina actual.
    """

    def __init__(self, config_path: Optional[str] = None):
        if not config_path:
            config_path = str(Path(__file__).resolve().parent.parent.parent / "config" / "cluster.yaml")
        self.config_path = Path(config_path)

    def inspect(self) -> Dict[str, Any]:
        cluster_specs = {}
        if self.config_path.is_file():
            with open(self.config_path, "r", encoding="utf-8") as f:
                cluster_specs = yaml.safe_load(f)

        local_specs = {
            "logical_cpus": psutil.cpu_count(logical=True),
            "physical_cpus": psutil.cpu_count(logical=False),
            "total_ram_gb": round(psutil.virtual_memory().total / (1024 ** 3), 2),
            "available_ram_gb": round(psutil.virtual_memory().available / (1024 ** 3), 2),
        }

        return {
            "cluster_specs": cluster_specs,
            "local_specs": local_specs
        }
