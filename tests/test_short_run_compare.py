"""tests/test_short_run_compare.py - Corrida corta siempre vía Slurm en el clúster y comparativas con signo."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console

from modules.engine.runner import EngineRunner
from modules.engine.storage import StorageEngine
from modules.inspectors.slurm_inspector import SlurmInspector, infer_run_type
from modules.reporters.console import ConsoleReporter
from shared.exceptions import ClusterAIError, SlurmCommandError
from shared.models import JobRun, RunType

SCRIPT = "#!/bin/bash\n#SBATCH -J sim\n#SBATCH -N 2\n#SBATCH -n 8\n#SBATCH -t 3-00:00:00\nsrun ./a.out\n"


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.delenv("CLUSTER_AI_MODE", raising=False)
    return EngineRunner(storage=StorageEngine(db_path=str(tmp_path / "t.db")))


@pytest.fixture
def script(tmp_path):
    path = tmp_path / "sim.sh"
    path.write_text(SCRIPT, encoding="utf-8")
    return path


def completed(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


# --- Corrida corta ---------------------------------------------------------------------

def test_short_run_never_executes_locally_on_the_cluster(runner, script):
    """Con Slurm disponible, --mock se rechaza: el nodo cabecera no es para cómputo."""
    with patch.object(SlurmInspector, "_is_slurm_available", return_value=True), \
         patch("modules.mock.simulator.SlurmSimulator._execute_local_job") as local_exec:
        with pytest.raises(ClusterAIError, match="no se ejecutan scripts en el nodo"):
            runner.short_run(str(script), "00:10:00", force_mock=True)
        local_exec.assert_not_called()


def test_short_run_submits_to_slurm_with_time_and_partition(runner, script):
    with patch.object(SlurmInspector, "_is_slurm_available", return_value=True), \
         patch("subprocess.run", return_value=completed("Submitted batch job 4300\n")) as run_mock:
        job_id = runner.short_run(str(script), "10")  # un número solo son minutos
    assert job_id == 4300
    cmd = run_mock.call_args[0][0]
    assert cmd[0] == "sbatch" and cmd[-1] == str(script)
    assert cmd[cmd.index("--time") + 1] == "00:10:00"
    assert cmd[cmd.index("--partition") + 1] == "short"       # la cola más corta que admite 10 min
    assert cmd[cmd.index("--job-name") + 1] == "sim-shortrun"
    assert "--wait" not in cmd


def test_short_run_wait_imports_real_telemetry(runner, script):
    job = JobRun(job_id=4301, user="ana", script_name="sim-shortrun", script_hash="", run_type=RunType.SHORT_RUN,
                 partition="short", nodes=2, cores=8, walltime_seconds=600, cpu_time_seconds=4000,
                 cpu_efficiency_pct=83.3, max_rss_bytes=2 * 1024 ** 3, core_hours=1.33)
    with patch.object(SlurmInspector, "_is_slurm_available", return_value=True), \
         patch("subprocess.run", return_value=completed("Submitted batch job 4301\n", returncode=1)) as run_mock, \
         patch.object(EngineRunner, "sync_job", return_value=job) as sync_mock:
        assert runner.short_run(str(script), "00:10:00", wait=True) == 4301
    assert "--wait" in run_mock.call_args[0][0]   # el job terminó por TIMEOUT (exit 1): igual se informa
    sync_mock.assert_called_once_with(4301, force_mock=False)


@pytest.mark.parametrize("duration", ["abc", "0", "UNLIMITED"])
def test_short_run_rejects_invalid_duration(runner, script, duration):
    with pytest.raises(ClusterAIError, match="Duración inválida"):
        runner.short_run(str(script), duration)


def test_short_run_rejects_duration_beyond_open_partitions(runner, script):
    with patch.object(SlurmInspector, "_is_slurm_available", return_value=True):
        with pytest.raises(ClusterAIError, match="no es una corrida corta"):
            runner.short_run(str(script), "8-00:00:00")


def test_sbatch_rejection_is_reported_with_slurm_message(script):
    inspector = SlurmInspector(allow_mock_fallback=False)
    msg = "sbatch: error: Batch job submission failed: User's group not permitted to use this partition"
    with patch("subprocess.run", return_value=completed("", msg, 1)):
        with pytest.raises(SlurmCommandError, match="User's group not permitted"):
            inspector.submit_job(str(script))


@pytest.mark.parametrize("name, expected", [
    ("sim-shortrun", RunType.SHORT_RUN),
    ("job.optimized.sh", RunType.OPTIMIZED),
    ("test", RunType.BASELINE),
])
def test_infer_run_type(name, expected):
    assert infer_run_type(name) == expected


# --- Comparativas ------------------------------------------------------------------------

def make_run(job_id, walltime, rss, run_type=RunType.BASELINE):
    return JobRun(job_id=job_id, user="ana", script_name="test", script_hash="", run_type=run_type,
                  partition="short", nodes=4, cores=8, walltime_seconds=walltime, cpu_time_seconds=0,
                  cpu_efficiency_pct=0, max_rss_bytes=rss, core_hours=8 * walltime / 3600)


def test_compare_labels_roles_declared_by_user(tmp_path):
    storage = StorageEngine(db_path=str(tmp_path / "c.db"))
    storage.save_run(make_run(4284, 1.0, 4_730_000))
    storage.save_run(make_run(4283, 1.0, 13_380_000))
    storage.label_comparison(4284, 4283)
    assert storage.get_run(4284).run_type == RunType.BASELINE
    assert storage.get_run(4283).run_type == RunType.OPTIMIZED

    storage.save_run(make_run(4290, 600, 1, RunType.SHORT_RUN))
    storage.label_comparison(4284, 4290)
    assert storage.get_run(4290).run_type == RunType.SHORT_RUN  # las corridas cortas conservan su tipo


def test_compare_reports_regressions_with_sign(tmp_path):
    storage = StorageEngine(db_path=str(tmp_path / "c.db"))
    storage.save_run(make_run(1, 100.0, 4_730_000))
    storage.save_run(make_run(2, 150.0, 13_380_000))   # el "optimizado" es más lento y usa más memoria
    comp = storage.compare_runs(1, 2)
    assert comp.walltime_saved_seconds == -50.0
    assert comp.memory_saved_bytes < 0

    console = Console(record=True, width=200, color_system=None)
    ConsoleReporter(console=console).render_comparison(comp)
    text = console.export_text()
    assert "más memoria" in text and "más lento" in text
    assert "-0 B" not in text
