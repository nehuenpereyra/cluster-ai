"""tests/test_sbatch_scope.py - Alcance de la fase actual (solo #SBATCH), rutas de base e inspector Slurm."""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from modules.engine.ai import OpenAICompatibleClient
from modules.engine.runner import EngineRunner
from modules.engine.storage import PROJECT_ROOT, StorageEngine, resolve_db_path
from modules.inspectors.slurm_inspector import SlurmInspector
from shared.features import SCOPE_FULL, SCOPE_SBATCH

SBATCH = "#!/bin/bash\n#SBATCH -J galaxias\n#SBATCH -p medium\n#SBATCH -t 02:00:00\npython simulacion.py\n"
PY_CODE = "for i in range(10):\n    total = total + i\n"


class RecordingAI(OpenAICompatibleClient):
    def __init__(self):
        super().__init__(api_key="sk-test-key-123456", model="modelo-test")
        self.calls = []

    def chat_completion(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return "Recomendaciones de directivas."


class FakeBackend:
    """Sustituto del helper privilegiado que registra las llamadas."""

    def __init__(self):
        self.reviews = []
        self.saved = []

    def review_sbatch(self, script_name, content, issues_summary=""):
        self.reviews.append((script_name, content))
        return "Revisión del servicio."

    def save_analysis(self, result, report_content):
        self.saved.append((result, report_content))
        return 1


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("CLUSTER_AI_ENABLE_CODE_AUDIT", raising=False)
    monkeypatch.delenv("CLUSTER_AI_MODE", raising=False)
    script = tmp_path / "job.sh"
    script.write_text(SBATCH, encoding="utf-8")
    (tmp_path / "simulacion.py").write_text(PY_CODE, encoding="utf-8")
    return script


def test_diagnose_defaults_to_sbatch_only(project, tmp_path):
    ai = RecordingAI()
    runner = EngineRunner(storage=StorageEngine(db_path=str(tmp_path / "t.db")), ai_client=ai)
    result = runner.diagnose(str(project))

    assert result.scope == SCOPE_SBATCH
    assert result.code_suggestions == []
    messages, kwargs = ai.calls[0]
    assert kwargs["scope"] == SCOPE_SBATCH
    assert PY_CODE not in messages[1]["content"]

    report = (tmp_path / "job_INFORME_MEJORA.md").read_text(encoding="utf-8")
    assert "no está habilitada en esta fase" in report


def test_diagnose_full_scope_only_when_enabled_locally(project, tmp_path, monkeypatch):
    monkeypatch.setenv("CLUSTER_AI_ENABLE_CODE_AUDIT", "true")
    ai = RecordingAI()
    runner = EngineRunner(storage=StorageEngine(db_path=str(tmp_path / "t.db")), ai_client=ai)
    result = runner.diagnose(str(project))

    assert result.scope == SCOPE_FULL
    assert PY_CODE.strip().splitlines()[0] in ai.calls[0][0][1]["content"]


def test_diagnose_with_helper_never_audits_code(project, tmp_path, monkeypatch):
    monkeypatch.setenv("CLUSTER_AI_ENABLE_CODE_AUDIT", "true")
    ai = RecordingAI()
    backend = FakeBackend()
    runner = EngineRunner(ai_client=ai, privileged_client=backend)
    result = runner.diagnose(str(project))

    assert result.scope == SCOPE_SBATCH
    assert ai.calls == []  # la API Key nunca se usa desde el proceso del investigador
    assert backend.reviews == [("job.sh", SBATCH)]
    assert backend.saved[0][1] == "Revisión del servicio."
    # Los entregables quedan junto al script, creados por el propio investigador
    assert (tmp_path / "job.optimized.sh").is_file()


def test_sbatch_fallback_has_no_code_advice(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = OpenAICompatibleClient()
    text = client.chat_completion([{"role": "user", "content": "x"}], scope=SCOPE_SBATCH)
    assert "Modo Contingencia" in text and "Zen 3" in text
    assert "numba" not in text and "NumPy" not in text


@patch("httpx.Client")
def test_ai_client_redacts_key_and_verifies_tls(mock_client_class):
    response = MagicMock(status_code=401, text="Incorrect API key provided: sk-secreta-0123456789")
    instance = MagicMock()
    instance.__enter__.return_value.post.return_value = response
    mock_client_class.return_value = instance

    client = OpenAICompatibleClient(api_key="sk-secreta-0123456789")
    text = client.chat_completion([{"role": "user", "content": "x"}])
    assert "sk-secreta-0123456789" not in text
    assert mock_client_class.call_args[1]["verify"] is True


def test_relative_db_path_is_anchored_to_project_not_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLUSTER_AI_MODE", "cluster")
    monkeypatch.setenv("CLUSTER_AI_DB_PATH", "cluster.db")
    assert resolve_db_path() == (PROJECT_ROOT / "cluster.db").resolve()


def test_storage_migrates_analysis_reports_user_column(tmp_path):
    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE analysis_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT, script_path TEXT NOT NULL, script_hash TEXT NOT NULL,
            backup_path TEXT, optimized_script_path TEXT, recommended_partition TEXT,
            report_markdown_path TEXT, report_content TEXT, timestamp TEXT NOT NULL)""")
    StorageEngine(db_path=str(db))
    with sqlite3.connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(analysis_reports)")}
    assert "user" in cols


@patch("subprocess.run")
def test_inspector_uses_absolute_binaries_and_skips_running_jobs(mock_run):
    mock_run.return_value = MagicMock(
        stdout="42|ana|job.sh|medium|1|16|60|900||0:0|RUNNING\n", returncode=0
    )
    inspector = SlurmInspector(bin_dir="/usr/bin", allow_mock_fallback=False)
    assert inspector.use_mock is False and inspector.simulator is None

    assert inspector.get_job_accounting(42) is None
    cmd = mock_run.call_args[0][0]
    assert cmd[0].replace("\\", "/") == "/usr/bin/sacct"


def test_diagnose_never_creates_backup_and_keeps_edited_optimized(project, tmp_path):
    """El original no se toca (sin .bak); una versión optimizada editada a mano no se pierde."""
    runner = EngineRunner(storage=StorageEngine(db_path=str(tmp_path / "t.db")), ai_client=RecordingAI())
    first = runner.diagnose(str(project))
    assert project.read_text(encoding="utf-8") == SBATCH
    assert not list(tmp_path.glob("*.bak*"))
    assert first.backup_path is None and first.previous_versions == []

    optimized = tmp_path / "job.optimized.sh"
    generated = optimized.read_text(encoding="utf-8")

    # Volver a diagnosticar sin cambios no duplica la versión optimizada
    runner.diagnose(str(project))
    assert not list(tmp_path.glob("job.optimized_*.sh"))

    # Si el investigador la editó, la edición se conserva con su fecha
    optimized.write_text(generated + "# ajuste manual\n", encoding="utf-8")
    third = runner.diagnose(str(project))
    kept = list(tmp_path.glob("job.optimized_*.sh"))
    assert len(kept) == 1 and "# ajuste manual" in kept[0].read_text(encoding="utf-8")
    assert optimized.read_text(encoding="utf-8") == generated
    assert str(kept[0]) in third.previous_versions
