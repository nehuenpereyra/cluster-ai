"""
modules/engine/privileged_client.py - Cliente del helper privilegiado `cluster-ai-priv`.

El CLI corre con la identidad del investigador. Para las únicas dos operaciones que requieren
privilegios (usar la API Key y escribir/leer la base de producción) invoca al helper mediante
`sudo -n -u cluster-ai /usr/local/libexec/cluster-ai-priv <subcomando>`, enviando JSON por stdin.
"""

import json
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Sequence

from shared.exceptions import ClusterAIError
from shared.features import DEFAULT_HELPER_PATH
from shared.models import AnalysisResult, BenchmarkComparison, JobRun
SERVICE_USER = "cluster-ai"
DEFAULT_TIMEOUT_SECONDS = 180


class PrivilegedServiceError(ClusterAIError):
    """Error informado por el helper privilegiado o al invocarlo."""


class PrivilegedClient:
    """Invoca al helper privilegiado vía sudo y traduce sus respuestas JSON."""

    def __init__(
        self,
        helper_path: str = DEFAULT_HELPER_PATH,
        service_user: str = SERVICE_USER,
        sudo_path: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.helper_path = helper_path
        self.service_user = service_user
        self.sudo_path = sudo_path or shutil.which("sudo") or "/usr/bin/sudo"
        self.timeout = timeout

    def call(self, action: str, args: Sequence[Any] = (), payload: Optional[Dict[str, Any]] = None) -> Any:
        cmd = [self.sudo_path, "-n", "-u", self.service_user, self.helper_path, action, *[str(a) for a in args]]
        try:
            proc = subprocess.run(
                cmd,
                input=json.dumps(payload or {}, ensure_ascii=True),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise PrivilegedServiceError(f"No se encontró sudo para invocar el servicio Cluster-AI: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise PrivilegedServiceError(
                f"El servicio Cluster-AI no respondió en {self.timeout} segundos."
            ) from exc

        try:
            response = json.loads(proc.stdout.strip() or "null")
        except json.JSONDecodeError:
            response = None

        if not isinstance(response, dict):
            detail = (proc.stderr or proc.stdout or "").strip()[:400]
            if detail.startswith("sudo:"):
                # p. ej. "sudo: a password is required": no existe la regla NOPASSWD para este usuario
                raise PrivilegedServiceError(
                    "No se pudo invocar el servicio Cluster-AI vía sudo. Verifique con el administrador "
                    f"que su usuario esté autorizado (regla en /etc/sudoers.d/cluster-ai). Detalle: {detail}"
                )
            raise PrivilegedServiceError(f"Respuesta inválida del servicio Cluster-AI: {detail or 'sin salida'}")

        if not response.get("ok"):
            raise PrivilegedServiceError(response.get("error") or "Error desconocido del servicio Cluster-AI.")
        return response.get("data")

    # Operaciones del contrato -------------------------------------------------

    def whoami(self) -> Dict[str, Any]:
        return self.call("whoami")

    def review_sbatch(self, script_name: str, sbatch_content: str, issues_summary: str = "") -> str:
        data = self.call("review-sbatch", payload={
            "script_name": script_name,
            "sbatch_content": sbatch_content,
            "issues_summary": issues_summary,
        })
        return data.get("content", "")

    def save_analysis(self, result: AnalysisResult, report_content: str) -> int:
        data = self.call("save-analysis", payload={
            "script_path": result.script_path,
            "script_hash": result.script_hash,
            "backup_path": result.backup_path,
            "optimized_script_path": result.optimized_script_path,
            "report_markdown_path": result.report_markdown_path,
            "recommended_partition": result.recommended_partition,
            "report_content": report_content,
        })
        return int(data.get("report_id", 0))

    def sync_job(self, job_id: int) -> JobRun:
        return JobRun.model_validate(self.call("sync-job", args=[int(job_id)]))

    def compare(self, baseline_id: int, optimized_id: int) -> BenchmarkComparison:
        return BenchmarkComparison.model_validate(
            self.call("compare", args=[int(baseline_id), int(optimized_id)])
        )

    def history(self, limit: int = 20, user: Optional[str] = None) -> List[JobRun]:
        data = self.call("history", payload={"limit": limit, "user": user})
        return [JobRun.model_validate(r) for r in data.get("runs", [])]


def detect_privileged_client() -> Optional[PrivilegedClient]:
    """
    Decide si el CLI debe delegar en el helper privilegiado.

    - `CLUSTER_AI_PRIV_HELPER=<ruta>`: usa ese lanzador.
    - `CLUSTER_AI_PRIV_HELPER=off` (o vacío): modo local de desarrollo (sin helper).
    - Sin la variable: se usa el helper si está instalado en DEFAULT_HELPER_PATH.
    """
    configured = os.getenv("CLUSTER_AI_PRIV_HELPER")
    if configured is not None:
        if configured.strip().lower() in ("", "off", "none", "0", "false"):
            return None
        return PrivilegedClient(helper_path=configured.strip())
    if os.name == "posix" and os.path.isfile(DEFAULT_HELPER_PATH):
        return PrivilegedClient()
    return None
