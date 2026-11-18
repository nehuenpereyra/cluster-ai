"""
modules/privileged/helper.py - Helper privilegiado mínimo de Cluster-AI.

Se ejecuta vía `sudo -u cluster-ai /usr/local/libexec/cluster-ai-priv <subcomando>` y es la
única pieza que accede a la API Key y a la base de datos de producción.

Reglas de seguridad (ver docs/configuracion_server.md):
  - La identidad del invocante se obtiene solo de SUDO_UID (nunca de USER/USERNAME/LOGNAME).
  - La entrada es JSON por stdin, acotada en tamaño y validada; los argumentos solo son
    el subcomando y Job IDs enteros.
  - No recibe rutas de archivo, no abre archivos del usuario y no ejecuta scripts ni sbatch.
  - La configuración se lee de una ruta absoluta fija, nunca del directorio actual ni del entorno.
  - En esta fase solo existe la revisión con IA del script #SBATCH.
"""

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, TextIO, Tuple

from dotenv import dotenv_values
from pydantic import ValidationError

from modules.engine.ai import OpenAICompatibleClient
from modules.engine.context import ContextAssembler
from modules.engine.storage import StorageEngine
from modules.inspectors.slurm_inspector import SlurmInspector
from shared.exceptions import ClusterAIError
from shared.features import SCOPE_SBATCH
from shared.models import (
    AnalysisRecordRequest,
    AnalysisResult,
    HistoryRequest,
    JobRun,
    SbatchReviewRequest,
)

DEFAULT_CONFIG_PATH = Path("/etc/cluster-ai/cluster-ai.env")
SERVICE_USER = "cluster-ai"
MAX_STDIN_CHARS = 256 * 1024
DEFAULT_SLURM_BIN_DIR = "/usr/bin"
DEFAULT_MAX_AI_PER_HOUR = 30
PROXY_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy")
_JOB_ID_RE = re.compile(r"^[1-9][0-9]{0,9}$")


class HelperError(Exception):
    """Error presentable al usuario (sin detalles internos ni secretos)."""


@dataclass(frozen=True)
class Invoker:
    """Usuario real que invocó el helper a través de sudo."""
    user: str
    uid: int
    is_admin: bool = False


@dataclass
class HelperConfig:
    """Configuración del helper, leída exclusivamente de DEFAULT_CONFIG_PATH."""
    db_path: Path
    api_key: str = ""
    base_url: Optional[str] = None
    model: Optional[str] = None
    tls_verify: bool = True
    slurm_bin_dir: str = DEFAULT_SLURM_BIN_DIR
    admin_group: Optional[str] = None
    max_ai_per_hour: int = DEFAULT_MAX_AI_PER_HOUR
    proxies: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Configuración e identidad
# ---------------------------------------------------------------------------

def load_config(path: Path) -> HelperConfig:
    """Lee y valida la configuración del servicio."""
    if not path.is_file():
        raise HelperError("El servicio Cluster-AI no está configurado en este servidor. Contacte al administrador.")
    values = {k: (v or "").strip() for k, v in dotenv_values(path).items()}

    db_raw = values.get("CLUSTER_AI_DB_PATH", "")
    if not db_raw or not os.path.isabs(db_raw):
        raise HelperError("Configuración inválida del servicio: CLUSTER_AI_DB_PATH debe ser una ruta absoluta.")

    slurm_bin_dir = values.get("CLUSTER_AI_SLURM_BIN_DIR") or DEFAULT_SLURM_BIN_DIR
    if not os.path.isabs(slurm_bin_dir):
        raise HelperError("Configuración inválida del servicio: CLUSTER_AI_SLURM_BIN_DIR debe ser una ruta absoluta.")

    try:
        max_ai = int(values.get("CLUSTER_AI_MAX_AI_REQUESTS_PER_HOUR") or DEFAULT_MAX_AI_PER_HOUR)
    except ValueError:
        max_ai = DEFAULT_MAX_AI_PER_HOUR

    return HelperConfig(
        db_path=Path(db_raw),
        api_key=values.get("OPENAI_API_KEY", ""),
        base_url=values.get("OPENAI_BASE_URL") or None,
        model=values.get("OPENAI_MODEL") or None,
        tls_verify=(values.get("OPENAI_TLS_VERIFY", "true").lower() not in ("0", "false", "no")),
        slurm_bin_dir=slurm_bin_dir,
        admin_group=values.get("CLUSTER_AI_ADMIN_GROUP") or None,
        max_ai_per_hour=max(0, max_ai),
        proxies={k: values[k] for k in PROXY_VARS if values.get(k)},
    )


def resolve_invoker_from_sudo(environ: Mapping[str, str], config: HelperConfig) -> Invoker:
    """
    Obtiene el usuario real a partir de SUDO_UID, que sudo fija con el UID real del proceso
    invocante y que el usuario no puede alterar. Además verifica que el proceso corra como
    el usuario de servicio (es decir, que efectivamente se haya entrado por sudo).
    """
    import grp
    import pwd

    try:
        service_uid = pwd.getpwnam(SERVICE_USER).pw_uid
    except KeyError:
        raise HelperError(f"No existe el usuario de servicio '{SERVICE_USER}' en este sistema.")
    if os.getuid() != service_uid:
        raise HelperError(f"Este helper debe ejecutarse mediante sudo como '{SERVICE_USER}'.")

    raw_uid = environ.get("SUDO_UID", "")
    if not raw_uid.isdigit():
        raise HelperError("Este helper solo puede ejecutarse a través de sudo (falta SUDO_UID).")
    uid = int(raw_uid)
    try:
        pw = pwd.getpwuid(uid)
    except KeyError:
        raise HelperError("No se pudo resolver el usuario invocante.")

    is_admin = uid == 0
    if config.admin_group and not is_admin:
        try:
            admin_gid = grp.getgrnam(config.admin_group).gr_gid
            is_admin = admin_gid in os.getgrouplist(pw.pw_name, pw.pw_gid)
        except KeyError:
            is_admin = False
    return Invoker(user=pw.pw_name, uid=uid, is_admin=is_admin)


# ---------------------------------------------------------------------------
# Servicios (inyectables para pruebas)
# ---------------------------------------------------------------------------

@dataclass
class HelperServices:
    storage: StorageEngine
    ai_client_factory: Callable[[], OpenAICompatibleClient]
    inspector_factory: Callable[[], SlurmInspector]
    context_assembler: ContextAssembler


def build_services(config: HelperConfig) -> HelperServices:
    """Construye los servicios reales a partir de la configuración del servidor."""
    # httpx toma el proxy de las variables de entorno; sudo las descarta, así que se
    # inyectan desde la configuración del servicio.
    for key, value in config.proxies.items():
        os.environ[key] = value

    return HelperServices(
        storage=StorageEngine(db_path=str(config.db_path)),
        ai_client_factory=lambda: OpenAICompatibleClient(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
            verify_tls=config.tls_verify,
        ),
        inspector_factory=lambda: SlurmInspector(
            force_mock=False,
            bin_dir=config.slurm_bin_dir,
            allow_mock_fallback=False,
        ),
        context_assembler=ContextAssembler(),
    )


# ---------------------------------------------------------------------------
# Subcomandos
# ---------------------------------------------------------------------------

def _parse_job_id(raw: str) -> int:
    if not _JOB_ID_RE.match(raw or ""):
        raise HelperError("Job ID inválido: debe ser un entero positivo.")
    return int(raw)


def _expect_args(args: List[str], count: int, usage: str) -> None:
    if len(args) != count:
        raise HelperError(f"Uso: cluster-ai-priv {usage}")


def _ensure_owner(run: JobRun, invoker: Invoker) -> None:
    if run.user != invoker.user and not invoker.is_admin:
        raise HelperError(f"El Job {run.job_id} pertenece a otro usuario.")


def _sync_job(job_id: int, invoker: Invoker, services: HelperServices) -> JobRun:
    """Consulta sacct como servicio, verifica la propiedad del job y guarda la telemetría."""
    job = services.inspector_factory().get_job_accounting(job_id)
    if job is None:
        raise HelperError(
            f"No se encontró información contable para el Job {job_id} en Slurm, o el trabajo todavía no finalizó."
        )
    _ensure_owner(job, invoker)
    services.storage.save_run(job)
    return job


def cmd_whoami(args, payload, invoker, config, services) -> Dict[str, Any]:
    _expect_args(args, 0, "whoami")
    return {"user": invoker.user, "is_admin": invoker.is_admin}


def cmd_review_sbatch(args, payload, invoker, config, services) -> Dict[str, Any]:
    _expect_args(args, 0, "review-sbatch < solicitud.json")
    req = SbatchReviewRequest.model_validate(payload)

    since = datetime.now() - timedelta(hours=1)
    used = services.storage.count_ai_usage_since(invoker.user, since)
    if used >= config.max_ai_per_hour:
        raise HelperError(
            f"Se alcanzó el límite de {config.max_ai_per_hour} consultas a la IA por hora. Intente nuevamente más tarde."
        )
    # Se registra antes de consultar: los intentos fallidos también consumen cupo.
    services.storage.record_ai_usage(invoker.user, "review-sbatch")

    messages = services.context_assembler.assemble_sbatch_prompt(
        req.script_name, req.sbatch_content, issues_summary=req.issues_summary
    )
    client = services.ai_client_factory()
    content = client.chat_completion(messages, temperature=0.6, max_tokens=2048, scope=SCOPE_SBATCH)
    return {"content": client._redact(content), "model": client.model}


def cmd_save_analysis(args, payload, invoker, config, services) -> Dict[str, Any]:
    _expect_args(args, 0, "save-analysis < analisis.json")
    req = AnalysisRecordRequest.model_validate(payload)
    result = AnalysisResult(
        script_path=req.script_path,
        script_hash=req.script_hash,
        backup_path=req.backup_path,
        optimized_script_path=req.optimized_script_path,
        report_markdown_path=req.report_markdown_path,
        recommended_partition=req.recommended_partition,
        scope=SCOPE_SBATCH,
    )
    report_id = services.storage.save_analysis(result, markdown_content=req.report_content, user=invoker.user)
    return {"report_id": report_id}


def cmd_sync_job(args, payload, invoker, config, services) -> Dict[str, Any]:
    _expect_args(args, 1, "sync-job <job_id>")
    job = _sync_job(_parse_job_id(args[0]), invoker, services)
    return job.model_dump(mode="json")


def cmd_compare(args, payload, invoker, config, services) -> Dict[str, Any]:
    _expect_args(args, 2, "compare <job_baseline> <job_optimizado>")
    ids = [_parse_job_id(a) for a in args]
    for job_id in ids:
        stored = services.storage.get_run(job_id)
        if stored is None:
            _sync_job(job_id, invoker, services)
        else:
            _ensure_owner(stored, invoker)
    services.storage.label_comparison(ids[0], ids[1])
    comparison = services.storage.compare_runs(ids[0], ids[1])
    return comparison.model_dump(mode="json")


def cmd_history(args, payload, invoker, config, services) -> Dict[str, Any]:
    _expect_args(args, 0, "history < consulta.json")
    req = HistoryRequest.model_validate(payload)
    if invoker.is_admin:
        user_filter = req.user
    else:
        if req.user and req.user != invoker.user:
            raise HelperError("Solo puede consultar su propio historial.")
        user_filter = invoker.user
    runs = services.storage.list_runs(limit=req.limit, user=user_filter)
    return {"runs": [r.model_dump(mode="json") for r in runs]}


COMMANDS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "whoami": cmd_whoami,
    "review-sbatch": cmd_review_sbatch,
    "save-analysis": cmd_save_analysis,
    "sync-job": cmd_sync_job,
    "compare": cmd_compare,
    "history": cmd_history,
}


# ---------------------------------------------------------------------------
# Ejecución
# ---------------------------------------------------------------------------

def _read_payload(stdin: TextIO) -> Dict[str, Any]:
    # Invocado a mano desde una terminal (p. ej. `whoami`): no esperar entrada interactiva
    if stdin is None or (hasattr(stdin, "isatty") and stdin.isatty()):
        return {}
    data = stdin.read(MAX_STDIN_CHARS + 1)
    if len(data) > MAX_STDIN_CHARS:
        raise HelperError(f"La entrada supera el máximo permitido ({MAX_STDIN_CHARS // 1024} KiB).")
    if not data.strip():
        return {}
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        raise HelperError("La entrada no es JSON válido.")
    if not isinstance(payload, dict):
        raise HelperError("La entrada debe ser un objeto JSON.")
    return payload


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(x) for x in err.get("loc", ())) or "entrada"
        parts.append(f"{loc}: {err.get('msg', 'inválido')}")
    return "Entrada inválida: " + "; ".join(parts)


def run(
    argv: List[str],
    stdin: TextIO,
    environ: Mapping[str, str],
    config_path: Path = DEFAULT_CONFIG_PATH,
    invoker_resolver: Callable[[Mapping[str, str], HelperConfig], Invoker] = resolve_invoker_from_sudo,
    services_factory: Callable[[HelperConfig], HelperServices] = build_services,
) -> Tuple[int, Dict[str, Any]]:
    """Ejecuta un subcomando y devuelve (código de salida, respuesta JSON)."""
    try:
        if not argv or argv[0] not in COMMANDS:
            raise HelperError("Subcomando desconocido. Opciones: " + ", ".join(sorted(COMMANDS)))
        handler = COMMANDS[argv[0]]

        config = load_config(config_path)
        invoker = invoker_resolver(environ, config)
        payload = _read_payload(stdin)
        services = services_factory(config)

        data = handler(argv[1:], payload, invoker, config, services)
        return 0, {"ok": True, "data": data}
    except HelperError as exc:
        return 1, {"ok": False, "error": str(exc)}
    except ValidationError as exc:
        return 1, {"ok": False, "error": _validation_message(exc)}
    except ClusterAIError as exc:
        return 1, {"ok": False, "error": str(exc)}
    except Exception as exc:  # pragma: no cover - salvaguarda: no filtrar detalles internos
        return 2, {"ok": False, "error": f"Error interno del servicio Cluster-AI ({type(exc).__name__})."}


def main(argv: Optional[List[str]] = None) -> int:
    code, response = run(argv if argv is not None else sys.argv[1:], sys.stdin, os.environ)
    # ASCII puro: independiente del locale que sudo deje en el entorno
    sys.stdout.write(json.dumps(response, ensure_ascii=True))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return code
