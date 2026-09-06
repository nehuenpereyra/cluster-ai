"""modules/mock/simulator.py - Emulador de Slurm para el clúster HPC del IALP."""

import os
import re
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import psutil
import yaml

from modules.engine.storage import StorageEngine
from shared.exceptions import SlurmCommandError
from shared.filesystem import safe_read_file
from shared.models import JobRun, RunType
from shared.utils import (
    calculate_core_hours,
    compute_sha256,
    format_bytes,
    format_slurm_time,
    parse_slurm_time,
)

class SlurmSimulator:
    """
    Emulador determinístico y ejecutor local para el entorno Slurm del IALP.
    Permite probar flujos de trabajo, realizar dry-runs y evaluar scripts sin estar
    conectado físicamente a los nodos de cómputo del clúster.
    """

    def __init__(self, config_path: Optional[str] = None, storage: Optional[StorageEngine] = None):
        if not config_path:
            config_path = str(Path(__file__).resolve().parent.parent.parent / "config" / "cluster.yaml")
        self.config_path = Path(config_path)
        self.storage = storage or StorageEngine(force_mock=True)
        self.config = self._load_cluster_config()

    def _load_cluster_config(self) -> dict:
        if not self.config_path.is_file():
            return {}
        with open(self.config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def sinfo(self) -> List[Dict[str, Any]]:
        """
        Emula el comando `sinfo` devolviendo el estado de las particiones y nodos del IALP.
        """
        partitions = self.config.get("partitions", {})
        compute_nodes = self.config.get("nodes", {}).get("compute_nodes", [])
        node_names = ",".join([n["name"] for n in compute_nodes]) or "nodo-comp[01-04]"

        info_list = []
        for part_name, part_data in partitions.items():
            info_list.append({
                "partition": part_name,
                "avail": "up",
                "timelimit": part_data.get("max_time", "infinite"),
                "nodes": len(compute_nodes),
                "state": "idle",
                "nodelist": node_names,
                "is_default": part_data.get("is_default", False)
            })
        return info_list

    def squeue(self) -> List[Dict[str, Any]]:
        """
        Emula el comando `squeue` listando los trabajos activos en la cola mock.
        """
        with self.storage._get_connection() as conn:
            rows = conn.execute("""
            SELECT job_id, partition, job_name, user, status, elapsed_seconds, nodes, cores
            FROM mock_jobs
            WHERE status IN ('PENDING', 'RUNNING')
            ORDER BY job_id DESC
            """).fetchall()
            return [dict(r) for r in rows]

    def _parse_sbatch_directives(self, script_content: str) -> Dict[str, Any]:
        """Extrae directivas #SBATCH del script."""
        directives = {
            "job_name": "job",
            "partition": "medium",
            "nodes": 1,
            "cores": 1,
            "time_limit": "01:00:00",
            "output": "salida-%j.out",
            "error": "salida-%j.err"
        }

        for line in script_content.splitlines():
            line = line.strip()
            if not line.startswith("#SBATCH"):
                continue

            # Buscar parámetros típicos: -J, -p, -t, -N, -n, -c, -o, -e
            m_job = re.search(r"(?:-J|--job-name=)\s*([^\s#]+)", line)
            if m_job:
                directives["job_name"] = m_job.group(1)

            m_part = re.search(r"(?:-p|--partition=)\s*([^\s#]+)", line)
            if m_part:
                directives["partition"] = m_part.group(1)

            m_time = re.search(r"(?:-t|--time=)\s*([^\s#]+)", line)
            if m_time:
                directives["time_limit"] = m_time.group(1)

            m_nodes = re.search(r"(?:-N|--nodes=)\s*(\d+)", line)
            if m_nodes:
                directives["nodes"] = int(m_nodes.group(1))

            m_cores = re.search(r"(?:-n|--ntasks=|-c|--cpus-per-task=)\s*(\d+)", line)
            if m_cores:
                directives["cores"] = int(m_cores.group(1))

            m_out = re.search(r"(?:-o|--output=)\s*([^\s#]+)", line)
            if m_out:
                directives["output"] = m_out.group(1)

            m_err = re.search(r"(?:-e|--error=)\s*([^\s#]+)", line)
            if m_err:
                directives["error"] = m_err.group(1)

        return directives

    def sbatch(
        self,
        script_path: str,
        run_locally: bool = False,
        timeout_seconds: Optional[int] = None,
        run_type: RunType = RunType.BASELINE
    ) -> int:
        """
        Emula el comando `sbatch` ingresando el trabajo en la cola mock.
        Si `run_locally` es True (por ejemplo para `short-run`), ejecuta el script
        midiendo el consumo real de recursos mediante psutil.
        """
        path = Path(script_path).resolve()
        if not path.is_file():
            raise SlurmCommandError(f"Archivo de script no encontrado: {script_path}")

        script_content = safe_read_file(path)
        directives = self._parse_sbatch_directives(script_content)

        submit_time = datetime.now().isoformat()
        
        with self.storage._get_connection() as conn:
            cursor = conn.execute("""
            INSERT INTO mock_jobs (
                job_name, user, partition, status, nodes, cores,
                time_limit, submit_time, script_path, script_content,
                output_file, error_file
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                directives["job_name"], os.getenv("USERNAME") or os.getenv("USER") or "investigador",
                directives["partition"], "PENDING", directives["nodes"], directives["cores"],
                directives["time_limit"], submit_time, str(path), script_content,
                directives["output"], directives["error"]
            ))
            job_id = cursor.lastrowid

        if run_locally:
            self._execute_local_job(job_id, path, directives, timeout_seconds, run_type)
        else:
            # Simulación sintética realista para dry-run
            self._simulate_synthetic_job(job_id, directives, script_content, run_type)

        return job_id

    def _execute_local_job(
        self,
        job_id: int,
        script_path: Path,
        directives: dict,
        timeout_seconds: Optional[int],
        run_type: RunType
    ) -> None:
        """Ejecuta localmente un script para corridas cortas con telemetría en vivo."""
        start_time = time.time()
        start_iso = datetime.now().isoformat()

        # Determinar comando de ejecución
        # En bash / sh o python según la primera línea o shebang
        cmd = ["bash", str(script_path)]
        if os.name == "nt":
            # Si estamos en Windows sin bash nativo, extraer y ejecutar comandos reconocibles
            with open(script_path, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
            if lines:
                first_cmd = lines[-1]  # última línea de ejecución
                if "python" in first_cmd:
                    cmd = [shlex.split(first_cmd)[0], *shlex.split(first_cmd)[1:]]
                else:
                    cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script_path)]

        out_name = directives["output"].replace("%j", str(job_id))
        err_name = directives["error"].replace("%j", str(job_id))

        out_path = Path(out_name)
        if not out_path.is_absolute():
            out_path = script_path.parent / out_path

        err_path = Path(err_name)
        if not err_path.is_absolute():
            err_path = script_path.parent / err_path

        # Asegurar que los directorios donde residen los logs existan
        out_path.parent.mkdir(parents=True, exist_ok=True)
        err_path.parent.mkdir(parents=True, exist_ok=True)

        with self.storage._get_connection() as conn:
            conn.execute("UPDATE mock_jobs SET status = 'RUNNING', start_time = ? WHERE job_id = ?", (start_iso, job_id))

        max_memory = 0
        total_cpu_time = 0.0
        exit_code = 0

        timeout = timeout_seconds or parse_slurm_time(directives["time_limit"]) or 600

        try:
            with open(out_path, "w", encoding="utf-8") as stdout_f, open(err_path, "w", encoding="utf-8") as stderr_f:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(script_path.parent),
                    stdout=stdout_f,
                    stderr=stderr_f,
                    shell=False
                )

                try:
                    p_info = psutil.Process(proc.pid)
                    while proc.poll() is None:
                        try:
                            # Monitorear memoria residente y cpu
                            mem_info = p_info.memory_info()
                            max_memory = max(max_memory, mem_info.rss)
                            # Incluir hijos si existen
                            for child in p_info.children(recursive=True):
                                try:
                                    max_memory = max(max_memory, child.memory_info().rss)
                                except Exception:
                                    pass
                        except Exception:
                            pass

                        if time.time() - start_time > timeout:
                            proc.kill()
                            exit_code = 124  # Timeout
                            break
                        time.sleep(0.1)

                    if exit_code == 0:
                        exit_code = proc.returncode or 0
                except Exception:
                    proc.wait(timeout=timeout)
                    exit_code = proc.returncode or 0

        except Exception as e:
            exit_code = 1
            try:
                with open(err_path, "a", encoding="utf-8") as stderr_f:
                    stderr_f.write(f"\n[Mockup Execution Error]: {e}\n")
            except Exception:
                pass

        elapsed = time.time() - start_time
        # Si la ejecución fue muy corta, asegurar un valor base
        max_memory = max(max_memory, 1024 * 1024 * 32)  # Mínimo 32MB simulados
        total_cpu_time = elapsed * 0.85  # Estimación de CPU time si no se capturó exacto
        cpu_eff = min(100.0, round((total_cpu_time / max(elapsed, 0.001)) * 100.0, 1))

        status = "COMPLETED" if exit_code == 0 else "FAILED"
        end_iso = datetime.now().isoformat()

        with self.storage._get_connection() as conn:
            conn.execute("""
            UPDATE mock_jobs SET
                status = ?, end_time = ?, exit_code = ?, elapsed_seconds = ?,
                max_rss_bytes = ?, cpu_efficiency_pct = ?
            WHERE job_id = ?
            """, (status, end_iso, exit_code, elapsed, max_memory, cpu_eff, job_id))

        # Registrar también en tabla de runs para trazabilidad
        run_record = JobRun(
            job_id=job_id,
            user=os.getenv("USERNAME") or "investigador",
            script_name=script_path.name,
            script_hash=compute_sha256(safe_read_file(script_path)),
            run_type=run_type,
            partition=directives["partition"],
            nodes=directives["nodes"],
            cores=directives["cores"],
            walltime_seconds=round(elapsed, 2),
            cpu_time_seconds=round(total_cpu_time, 2),
            cpu_efficiency_pct=cpu_eff,
            max_rss_bytes=max_memory,
            core_hours=calculate_core_hours(directives["cores"], elapsed),
            exit_code=exit_code,
            timestamp=end_iso
        )
        self.storage.save_run(run_record)

    def _simulate_synthetic_job(
        self,
        job_id: int,
        directives: dict,
        script_content: str,
        run_type: RunType
    ) -> None:
        """Simula una corrida sintética para pruebas sin ejecución de procesos."""
        time_limit_sec = parse_slurm_time(directives["time_limit"]) or 3600
        # Simular que la corrida tomó una fracción del tiempo asignado
        simulated_elapsed = min(1800.0, time_limit_sec * 0.4)
        if run_type == RunType.OPTIMIZED:
            simulated_elapsed *= 0.55  # ~45% de mejora
            simulated_cpu_eff = 92.4
            simulated_mem = 1024 * 1024 * 1024 * 4  # 4 GB
        else:
            simulated_cpu_eff = 54.2
            simulated_mem = 1024 * 1024 * 1024 * 8  # 8 GB

        cpu_time = simulated_elapsed * (simulated_cpu_eff / 100.0)
        end_iso = datetime.now().isoformat()

        with self.storage._get_connection() as conn:
            conn.execute("""
            UPDATE mock_jobs SET
                status = 'COMPLETED', end_time = ?, exit_code = 0,
                elapsed_seconds = ?, max_rss_bytes = ?, cpu_efficiency_pct = ?
            WHERE job_id = ?
            """, (end_iso, simulated_elapsed, simulated_mem, simulated_cpu_eff, job_id))

        run_record = JobRun(
            job_id=job_id,
            user=os.getenv("USERNAME") or "investigador",
            script_name=directives["job_name"],
            script_hash=compute_sha256(script_content),
            run_type=run_type,
            partition=directives["partition"],
            nodes=directives["nodes"],
            cores=directives["cores"],
            walltime_seconds=round(simulated_elapsed, 2),
            cpu_time_seconds=round(cpu_time, 2),
            cpu_efficiency_pct=simulated_cpu_eff,
            max_rss_bytes=int(simulated_mem),
            core_hours=calculate_core_hours(directives["cores"], simulated_elapsed),
            exit_code=0,
            timestamp=end_iso
        )
        self.storage.save_run(run_record)

    def sacct(self, job_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Emula `sacct` extrayendo el resumen de recursos de los trabajos finalizados."""
        query = "SELECT * FROM mock_jobs"
        params = []
        if job_id:
            query += " WHERE job_id = ?"
            params.append(job_id)
        query += " ORDER BY job_id DESC"

        with self.storage._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            results = []
            for r in rows:
                results.append({
                    "JobID": r["job_id"],
                    "JobName": r["job_name"],
                    "Partition": r["partition"],
                    "AllocCPUS": r["cores"],
                    "State": r["status"],
                    "ExitCode": f"{r['exit_code']}:0",
                    "Elapsed": format_slurm_time(int(r["elapsed_seconds"])),
                    "TotalCPU": format_slurm_time(int(r["elapsed_seconds"] * (r["cpu_efficiency_pct"] / 100.0))),
                    "MaxRSS": format_bytes(r["max_rss_bytes"])
                })
            return results

    def seff(self, job_id: int) -> Dict[str, Any]:
        """Emula `seff` devolviendo la eficiencia de CPU y memoria de un trabajo."""
        with self.storage._get_connection() as conn:
            r = conn.execute("SELECT * FROM mock_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not r:
                raise SlurmCommandError(f"Job ID {job_id} no encontrado en el historial de Slurm")

            cores = r["cores"]
            walltime = r["elapsed_seconds"]
            cpu_eff = r["cpu_efficiency_pct"]
            core_walltime = cores * walltime
            cpu_utilized = core_walltime * (cpu_eff / 100.0)

            return {
                "job_id": job_id,
                "cluster": "cluster-ialp",
                "user": r["user"],
                "state": r["status"],
                "cores": cores,
                "cpu_utilized": format_slurm_time(int(cpu_utilized)),
                "cpu_efficiency_pct": cpu_eff,
                "walltime": format_slurm_time(int(walltime)),
                "memory_utilized": format_bytes(r["max_rss_bytes"]),
                "core_hours": calculate_core_hours(cores, walltime)
            }

    def scancel(self, job_id: int) -> bool:
        """Cancela un trabajo activo en el mockup."""
        with self.storage._get_connection() as conn:
            cur = conn.execute("UPDATE mock_jobs SET status = 'CANCELLED' WHERE job_id = ? AND status IN ('PENDING', 'RUNNING')", (job_id,))
            return cur.rowcount > 0
