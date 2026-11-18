"""tests/test_privileged.py - Pruebas del helper privilegiado (cluster-ai-priv) y su cliente."""

import io
import json
import os
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from modules.engine.ai import OpenAICompatibleClient
from modules.engine.context import ContextAssembler
from modules.engine.privileged_client import PrivilegedClient, PrivilegedServiceError, detect_privileged_client
from modules.engine.storage import StorageEngine
from modules.privileged.helper import HelperError, HelperServices, Invoker, load_config, run
from shared.models import JobRun, RunType

SBATCH = "#!/bin/bash\n#SBATCH -J galaxias\n#SBATCH -p medium\n#SBATCH -t 02:00:00\npython sim.py\n"
SHA = "a" * 64


class FakeAI(OpenAICompatibleClient):
    """Cliente IA falso: registra los mensajes recibidos y responde un texto fijo."""

    def __init__(self):
        super().__init__(api_key="sk-secreta-0123456789", base_url="https://ia.example/v1", model="modelo-test")
        self.calls = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return "## Mejoras en las Directivas #SBATCH\n- usar --cpus-per-task"


class FakeInspector:
    def __init__(self, jobs):
        self.jobs = jobs

    def get_job_accounting(self, job_id):
        return self.jobs.get(job_id)


def make_job(job_id, user, walltime=3600.0, run_type=RunType.BASELINE):
    return JobRun(
        job_id=job_id, user=user, script_name=f"job{job_id}.sh", script_hash="",
        run_type=run_type, partition="medium", nodes=1, cores=16,
        walltime_seconds=walltime, cpu_time_seconds=walltime * 8, cpu_efficiency_pct=50.0,
        max_rss_bytes=1024, core_hours=16 * walltime / 3600.0,
    )


@pytest.fixture
def env(tmp_path):
    """Configuración, servicios falsos y función para invocar el helper como un usuario dado."""
    db_path = tmp_path / "var" / "cluster.db"
    db_path.parent.mkdir()
    config_path = tmp_path / "cluster-ai.env"
    config_path.write_text(
        f"OPENAI_API_KEY=sk-secreta-0123456789\n"
        f"CLUSTER_AI_DB_PATH={db_path}\n"
        f"CLUSTER_AI_MAX_AI_REQUESTS_PER_HOUR=2\n"
        f"HTTPS_PROXY=http://proxy.local:3128\n",
        encoding="utf-8",
    )
    storage = StorageEngine(db_path=str(db_path))
    ai = FakeAI()
    jobs = {}
    services = HelperServices(
        storage=storage,
        ai_client_factory=lambda: ai,
        inspector_factory=lambda: FakeInspector(jobs),
        context_assembler=ContextAssembler(),
    )

    def call(argv, payload=None, user="ana", is_admin=False, raw=None):
        stdin = io.StringIO(raw if raw is not None else (json.dumps(payload) if payload is not None else ""))
        return run(
            argv, stdin, environ={},
            config_path=config_path,
            invoker_resolver=lambda environ, cfg: Invoker(user=user, uid=1001, is_admin=is_admin),
            services_factory=lambda cfg: services,
        )

    return SimpleNamespace(call=call, storage=storage, ai=ai, jobs=jobs, db_path=db_path, config_path=config_path)


# --- Configuración ----------------------------------------------------------------

def test_load_config_requires_absolute_db_path(tmp_path):
    cfg = tmp_path / "c.env"
    cfg.write_text("CLUSTER_AI_DB_PATH=cluster.db\n", encoding="utf-8")
    with pytest.raises(HelperError, match="ruta absoluta"):
        load_config(cfg)


def test_load_config_missing_file(tmp_path):
    with pytest.raises(HelperError, match="no está configurado"):
        load_config(tmp_path / "inexistente.env")


def test_load_config_reads_proxy_and_limits(env):
    cfg = load_config(env.config_path)
    assert cfg.max_ai_per_hour == 2
    assert cfg.proxies == {"HTTPS_PROXY": "http://proxy.local:3128"}
    assert cfg.tls_verify is True


def test_unknown_subcommand(env):
    code, resp = env.call(["rm-rf"])
    assert code == 1 and not resp["ok"]
    assert "Subcomando desconocido" in resp["error"]


def test_whoami_uses_invoker_identity(env):
    code, resp = env.call(["whoami"], user="npereyra")
    assert code == 0
    assert resp["data"] == {"user": "npereyra", "is_admin": False}


@pytest.mark.skipif(os.name != "posix", reason="Requiere pwd/grp (POSIX)")
def test_resolve_invoker_rejects_when_not_service_user():
    from modules.privileged.helper import HelperConfig, resolve_invoker_from_sudo

    cfg = HelperConfig(db_path=__import__("pathlib").Path("/tmp/x.db"))
    with pytest.raises(HelperError):
        resolve_invoker_from_sudo({"SUDO_UID": "0"}, cfg)


# --- review-sbatch -----------------------------------------------------------------

def test_review_sbatch_builds_sbatch_only_prompt(env):
    code, resp = env.call(["review-sbatch"], {"script_name": "job.sh", "sbatch_content": SBATCH, "issues_summary": "- x"})
    assert code == 0, resp
    assert "Directivas" in resp["data"]["content"]

    messages, kwargs = env.ai.calls[0]
    assert kwargs["scope"] == "sbatch"
    prompt = messages[1]["content"]
    assert "#SBATCH -J galaxias" in prompt
    # No se incluyen reglas de optimización de código fuente ni archivos del proyecto
    assert "python_hpc.xml" not in prompt
    assert "Código Fuente Asociado" not in prompt


def test_review_sbatch_rate_limit(env):
    payload = {"script_name": "job.sh", "sbatch_content": SBATCH}
    assert env.call(["review-sbatch"], payload)[0] == 0
    assert env.call(["review-sbatch"], payload)[0] == 0
    code, resp = env.call(["review-sbatch"], payload)
    assert code == 1
    assert "límite de 2 consultas" in resp["error"]
    # El límite es por usuario
    assert env.call(["review-sbatch"], payload, user="otro")[0] == 0


@pytest.mark.parametrize("payload", [
    {"script_name": "../../etc/passwd", "sbatch_content": SBATCH},
    {"script_name": "/opt/cluster-ai/.env", "sbatch_content": SBATCH},
    {"script_name": "job.sh", "sbatch_content": ""},
    {"script_name": "job.sh", "sbatch_content": "x" * (64 * 1024 + 1)},
    {"script_name": "job.sh", "sbatch_content": SBATCH, "messages": [{"role": "user", "content": "hola"}]},
])
def test_review_sbatch_rejects_invalid_input(env, payload):
    code, resp = env.call(["review-sbatch"], payload)
    assert code == 1
    assert "Entrada inválida" in resp["error"]
    assert env.ai.calls == []


def test_rejects_oversized_or_non_object_stdin(env):
    code, resp = env.call(["review-sbatch"], raw="x" * (256 * 1024 + 10))
    assert code == 1 and "máximo permitido" in resp["error"]
    code, resp = env.call(["review-sbatch"], raw="[1, 2]")
    assert code == 1 and "objeto JSON" in resp["error"]


# --- save-analysis -----------------------------------------------------------------

def test_save_analysis_records_real_user(env):
    payload = {"script_path": "/home/ana/job.sh", "script_hash": SHA, "report_content": "informe"}
    code, resp = env.call(["save-analysis"], payload, user="ana")
    assert code == 0, resp
    with sqlite3.connect(env.db_path) as conn:
        row = conn.execute("SELECT user, report_content FROM analysis_reports").fetchone()
    assert row == ("ana", "informe")


def test_save_analysis_cannot_spoof_user(env):
    payload = {"script_path": "/home/ana/job.sh", "script_hash": SHA, "user": "otro"}
    code, resp = env.call(["save-analysis"], payload)
    assert code == 1 and "Entrada inválida" in resp["error"]


# --- sync-job / compare / history ---------------------------------------------------

def test_sync_job_own_job(env):
    env.jobs[500] = make_job(500, "ana")
    code, resp = env.call(["sync-job", "500"], user="ana")
    assert code == 0, resp
    assert resp["data"]["user"] == "ana"
    assert env.storage.get_run(500) is not None


def test_sync_job_rejects_foreign_job(env):
    env.jobs[501] = make_job(501, "otro")
    code, resp = env.call(["sync-job", "501"], user="ana")
    assert code == 1
    assert "pertenece a otro usuario" in resp["error"]
    assert env.storage.get_run(501) is None


def test_sync_job_admin_can_import_any_job(env):
    env.jobs[502] = make_job(502, "otro")
    code, _ = env.call(["sync-job", "502"], user="admin", is_admin=True)
    assert code == 0


@pytest.mark.parametrize("arg", ["abc", "-1", "0", "1;id", "99999999999"])
def test_sync_job_rejects_invalid_ids(env, arg):
    code, resp = env.call(["sync-job", arg])
    assert code == 1 and "Job ID inválido" in resp["error"]


def test_sync_job_unfinished_or_missing(env):
    code, resp = env.call(["sync-job", "777"])
    assert code == 1 and "no finalizó" in resp["error"]


def test_compare_syncs_and_checks_ownership(env):
    env.jobs[10] = make_job(10, "ana", walltime=3600)
    env.jobs[11] = make_job(11, "ana", walltime=1800, run_type=RunType.OPTIMIZED)
    code, resp = env.call(["compare", "10", "11"], user="ana")
    assert code == 0, resp
    assert resp["data"]["speedup"] == 2.0

    env.storage.save_run(make_job(12, "otro"))
    code, resp = env.call(["compare", "10", "12"], user="ana")
    assert code == 1 and "pertenece a otro usuario" in resp["error"]


def test_history_only_own_runs_for_regular_users(env):
    env.storage.save_run(make_job(20, "ana"))
    env.storage.save_run(make_job(21, "otro"))

    code, resp = env.call(["history"], {"limit": 10}, user="ana")
    assert code == 0
    assert [r["job_id"] for r in resp["data"]["runs"]] == [20]

    code, resp = env.call(["history"], {"user": "otro"}, user="ana")
    assert code == 1 and "propio historial" in resp["error"]

    code, resp = env.call(["history"], {"limit": 10}, user="admin", is_admin=True)
    assert {r["job_id"] for r in resp["data"]["runs"]} == {20, 21}


# --- Cliente (lado investigador) ----------------------------------------------------

def _completed(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_privileged_client_builds_sudo_command_and_parses_response():
    client = PrivilegedClient(helper_path="/usr/local/libexec/cluster-ai-priv", sudo_path="/usr/bin/sudo")
    with patch("subprocess.run", return_value=_completed(json.dumps({"ok": True, "data": {"content": "ok"}}))) as run_mock:
        assert client.review_sbatch("job.sh", SBATCH, "") == "ok"
    cmd = run_mock.call_args[0][0]
    assert cmd == ["/usr/bin/sudo", "-n", "-u", "cluster-ai", "/usr/local/libexec/cluster-ai-priv", "review-sbatch"]
    sent = json.loads(run_mock.call_args[1]["input"])
    assert sent["script_name"] == "job.sh" and sent["sbatch_content"] == SBATCH


def test_privileged_client_propagates_helper_error():
    client = PrivilegedClient(sudo_path="/usr/bin/sudo")
    with patch("subprocess.run", return_value=_completed(json.dumps({"ok": False, "error": "límite"}), returncode=1)):
        with pytest.raises(PrivilegedServiceError, match="límite"):
            client.sync_job(5)


def test_privileged_client_reports_missing_sudo_rule():
    client = PrivilegedClient(sudo_path="/usr/bin/sudo")
    with patch("subprocess.run", return_value=_completed("", "sudo: a password is required\n", 1)):
        with pytest.raises(PrivilegedServiceError, match="autorizado"):
            client.whoami()


def test_detect_privileged_client(monkeypatch):
    monkeypatch.setenv("CLUSTER_AI_PRIV_HELPER", "off")
    assert detect_privileged_client() is None
    monkeypatch.setenv("CLUSTER_AI_PRIV_HELPER", "/opt/otro/helper")
    assert detect_privileged_client().helper_path == "/opt/otro/helper"
