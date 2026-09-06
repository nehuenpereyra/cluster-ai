"""modules/inspectors/slurm_inspector.py - Inspector de telemetría y estado Slurm."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
from modules.inspectors.base import BaseInspector
from modules.mock.simulator import SlurmSimulator
from shared.exceptions import SlurmCommandError
from shared.models import RunType

class SlurmInspector(BaseInspector):
    """
    Inspecciona el estado en vivo de la cola Slurm del clúster IALP.
    Si los binarios de Slurm no están disponibles o el modo es 'mock', delega en SlurmSimulator.
    """

    def __init__(self, force_mock: bool = False):
        env_mode = os.getenv("CLUSTER_AI_MODE", "cluster").lower()
        self.use_mock = force_mock or env_mode == "mock" or not self._is_slurm_available()
        self.simulator = SlurmSimulator() if self.use_mock else None

    def _is_slurm_available(self) -> bool:
        """Verifica si los comandos sinfo y squeue existen en el sistema operativo."""
        return shutil.which("sinfo") is not None and shutil.which("squeue") is not None

    def inspect(self) -> Dict[str, Any]:
        """Obtiene un panorama general del clúster y la cola de trabajos."""
        return {
            "mode": "mock" if self.use_mock else "cluster",
            "partitions": self.get_partitions(),
            "active_jobs": self.get_active_jobs()
        }

    def get_partitions(self) -> List[Dict[str, Any]]:
        """Devuelve el estado de las particiones (sinfo)."""
        if self.use_mock:
            return self.simulator.sinfo()

        try:
            res = subprocess.run(
                ["sinfo", "--format=%P|%a|%l|%D|%T|%N", "--noheader"],
                capture_output=True,
                text=True,
                check=True
            )
            partitions = []
            for line in res.stdout.splitlines():
                parts = line.strip().split("|")
                if len(parts) >= 6:
                    partitions.append({
                        "partition": parts[0].replace("*", ""),
                        "avail": parts[1],
                        "timelimit": parts[2],
                        "nodes": int(parts[3]) if parts[3].isdigit() else 1,
                        "state": parts[4],
                        "nodelist": parts[5],
                        "is_default": "*" in parts[0]
                    })
            return partitions
        except Exception as e:
            # Fallback seguro a simulador
            return self.simulator.sinfo() if self.simulator else []

    def get_active_jobs(self) -> List[Dict[str, Any]]:
        """Devuelve los trabajos en ejecución y pendientes (squeue)."""
        if self.use_mock:
            return self.simulator.squeue()

        try:
            res = subprocess.run(
                ["squeue", "--format=%i|%P|%j|%u|%T|%M|%D|%C", "--noheader"],
                capture_output=True,
                text=True,
                check=True
            )
            jobs = []
            for line in res.stdout.splitlines():
                parts = line.strip().split("|")
                if len(parts) >= 8:
                    jobs.append({
                        "job_id": int(parts[0]) if parts[0].isdigit() else 0,
                        "partition": parts[1],
                        "job_name": parts[2],
                        "user": parts[3],
                        "status": parts[4],
                        "elapsed_seconds": parts[5],
                        "nodes": int(parts[6]) if parts[6].isdigit() else 1,
                        "cores": int(parts[7]) if parts[7].isdigit() else 1
                    })
            return jobs
        except Exception:
            return self.simulator.squeue() if self.simulator else []

    def submit_job(
        self,
        script_path: str,
        run_locally: bool = False,
        timeout_seconds: Optional[int] = None,
        run_type: Optional[RunType] = None
    ) -> int:
        """Envía un script a la cola del clúster (o mockup)."""
        if run_type is None:
            run_type = RunType.OPTIMIZED if ".optimized" in Path(script_path).name.lower() else RunType.BASELINE

        if self.use_mock:
            return self.simulator.sbatch(script_path, run_locally=run_locally, timeout_seconds=timeout_seconds, run_type=run_type)

        try:
            res = subprocess.run(["sbatch", script_path], capture_output=True, text=True, check=True)
            # Salida típica: "Submitted batch job 12345"
            out = res.stdout.strip()
            import re
            m = re.search(r"Submitted batch job (\d+)", out)
            if m:
                return int(m.group(1))
            raise SlurmCommandError(f"No se pudo parsear el Job ID de la respuesta: {out}")
        except Exception as e:
            raise SlurmCommandError(f"Fallo al enviar sbatch: {e}") from e

    def get_job_efficiency(self, job_id: int) -> Dict[str, Any]:
        """Obtiene la eficiencia de CPU y memoria (seff)."""
        if self.use_mock:
            return self.simulator.seff(job_id)

        try:
            res = subprocess.run(["seff", str(job_id)], capture_output=True, text=True, check=True)
            # Parsear salida estándar de seff
            lines = res.stdout.splitlines()
            eff_data: Dict[str, Any] = {"job_id": job_id, "raw": res.stdout}
            for l in lines:
                if "CPU Efficiency:" in l:
                    import re
                    m = re.search(r"(\d+\.?\d*)%", l)
                    if m:
                        eff_data["cpu_efficiency_pct"] = float(m.group(1))
                if "Memory Utilized:" in l:
                    eff_data["memory_utilized"] = l.split(":", 1)[1].strip()
            return eff_data
        except Exception:
            return self.simulator.seff(job_id) if self.simulator else {}
