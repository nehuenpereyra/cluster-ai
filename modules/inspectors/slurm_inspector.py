"""modules/inspectors/slurm_inspector.py - Inspector de telemetría y estado Slurm."""

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from modules.inspectors.base import BaseInspector
from modules.mock.simulator import SlurmSimulator
from shared.exceptions import SlurmCommandError
from shared.models import JobRun, RunType
from shared.utils import calculate_core_hours, parse_slurm_memory

# Estados de Slurm en los que el trabajo todavía no tiene telemetría definitiva
UNFINISHED_JOB_STATES = {
    "PENDING", "RUNNING", "REQUEUED", "SUSPENDED", "CONFIGURING",
    "COMPLETING", "RESIZING", "SIGNALING", "STAGE_OUT", "REQUEUE_HOLD",
}

class SlurmInspector(BaseInspector):
    """
    Inspecciona el estado en vivo de la cola Slurm del clúster IALP.
    Si los binarios de Slurm no están disponibles o el modo es 'mock', delega en SlurmSimulator.
    """

    def __init__(
        self,
        force_mock: bool = False,
        bin_dir: Optional[str] = None,
        allow_mock_fallback: bool = True
    ):
        """
        Args:
            force_mock: fuerza el uso del emulador Slurm-Mockup.
            bin_dir: directorio absoluto de los binarios de Slurm (p. ej. /usr/bin). Si se
                indica, los comandos se invocan por ruta absoluta en lugar de buscarse en el PATH.
            allow_mock_fallback: si es False (helper privilegiado), nunca conmuta al emulador:
                siempre consulta al Slurm real.
        """
        self.bin_dir = bin_dir
        if not allow_mock_fallback:
            self.use_mock = False
        else:
            env_mode = os.getenv("CLUSTER_AI_MODE", "cluster").lower()
            self.use_mock = force_mock or env_mode == "mock" or not self._is_slurm_available()
        self.simulator = SlurmSimulator() if self.use_mock else None

    def _cmd(self, name: str) -> str:
        """Devuelve el comando de Slurm, con ruta absoluta si se configuró `bin_dir`."""
        return str(Path(self.bin_dir) / name) if self.bin_dir else name

    def _is_slurm_available(self) -> bool:
        """Verifica si los comandos sinfo y squeue existen en el sistema operativo."""
        if self.bin_dir:
            return all((Path(self.bin_dir) / n).is_file() for n in ("sinfo", "squeue"))
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
                [self._cmd("sinfo"), "--format=%P|%a|%l|%D|%T|%N", "--noheader"],
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
                [self._cmd("squeue"), "--format=%i|%P|%j|%u|%T|%M|%D|%C", "--noheader"],
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
            res = subprocess.run([self._cmd("sbatch"), script_path], capture_output=True, text=True, check=True)
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
            res = subprocess.run([self._cmd("seff"), str(job_id)], capture_output=True, text=True, check=True)
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

    def get_job_accounting(self, job_id: int) -> Optional[JobRun]:
        """
        Consulta la base de datos contable de Slurm (vía `sacct` y `seff`)
        o el simulador mockup para extraer la telemetría completa de un Job.
        """
        if self.use_mock and self.simulator:
            rows = self.simulator.sacct(job_id)
            if not rows:
                return None
            row = rows[0]
            elapsed_sec = float(row.get("elapsed_seconds", 0.0))
            cores = int(row.get("cores", 1))
            run_type = RunType.OPTIMIZED if ".optimized" in str(row.get("script_name", "")).lower() else RunType.BASELINE
            eff_pct = float(row.get("cpu_efficiency_pct", 85.0))
            return JobRun(
                job_id=job_id,
                user=row.get("user") or "investigador",
                script_name=row.get("script_name") or f"job_{job_id}.sh",
                script_hash="",
                run_type=run_type,
                partition=row.get("partition", "medium"),
                nodes=int(row.get("nodes", 1)),
                cores=cores,
                walltime_seconds=elapsed_sec,
                cpu_time_seconds=round(elapsed_sec * (eff_pct / 100.0), 2),
                cpu_efficiency_pct=eff_pct,
                max_rss_bytes=int(row.get("max_rss_bytes", 0)),
                core_hours=calculate_core_hours(cores, elapsed_sec),
                exit_code=int(row.get("exit_code", 0)),
                timestamp=row.get("end_time") or datetime.now().isoformat()
            )

        # Consulta a Slurm real (sacct)
        try:
            cmd = [
                self._cmd("sacct"),
                "-j", str(job_id),
                "--parsable2",
                "--noheader",
                "--format=JobID,User,JobName,Partition,AllocNodes,AllocCPUS,ElapsedRaw,CPUTimeRAW,MaxRSS,ExitCode,State"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
            if not lines:
                return None

            main_parts = None
            max_rss_bytes = 0

            for l in lines:
                parts = l.split("|")
                if len(parts) < 10:
                    continue
                jid = parts[0]
                if jid == str(job_id):
                    main_parts = parts
                rss_str = parts[8]
                if rss_str:
                    p_rss = parse_slurm_memory(rss_str)
                    if p_rss > max_rss_bytes:
                        max_rss_bytes = p_rss

            if not main_parts:
                main_parts = lines[0].split("|")

            # Solo se registra telemetría de trabajos finalizados
            state = main_parts[10].split()[0].upper() if len(main_parts) > 10 and main_parts[10].strip() else ""
            if state in UNFINISHED_JOB_STATES:
                return None

            user = main_parts[1] or "investigador"
            job_name = main_parts[2] or f"job_{job_id}"
            partition = main_parts[3] or "medium"
            nodes = int(main_parts[4]) if main_parts[4].isdigit() else 1
            cores = int(main_parts[5]) if main_parts[5].isdigit() else 1
            elapsed_raw = float(main_parts[6]) if main_parts[6].isdigit() else 0.0
            cputime_raw = float(main_parts[7]) if main_parts[7].isdigit() else 0.0
            exit_code_str = main_parts[9].split(":")[0] if ":" in main_parts[9] else main_parts[9]
            exit_code = int(exit_code_str) if exit_code_str.isdigit() else 0

            # Obtener cpu_efficiency_pct de seff o calcular
            eff_data = self.get_job_efficiency(job_id)
            cpu_eff = eff_data.get("cpu_efficiency_pct")
            if cpu_eff is None:
                if elapsed_raw > 0 and cores > 0:
                    cpu_eff = round((cputime_raw / (elapsed_raw * cores)) * 100.0, 2)
                else:
                    cpu_eff = 0.0

            run_type = RunType.OPTIMIZED if ".optimized" in job_name.lower() else RunType.BASELINE

            return JobRun(
                job_id=job_id,
                user=user,
                script_name=job_name,
                script_hash="",
                run_type=run_type,
                partition=partition,
                nodes=nodes,
                cores=cores,
                walltime_seconds=elapsed_raw,
                cpu_time_seconds=cputime_raw,
                cpu_efficiency_pct=cpu_eff,
                max_rss_bytes=max_rss_bytes,
                core_hours=calculate_core_hours(cores, elapsed_raw),
                exit_code=exit_code,
                timestamp=datetime.now().isoformat()
            )
        except Exception:
            return None

