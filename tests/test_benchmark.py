"""tests/test_benchmark.py - Envío simultáneo de original y optimizado, espera y comparativa (`cluster-ai benchmark`)."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from modules.cli.main import app
from modules.engine.runner import EngineRunner
from modules.engine.storage import StorageEngine
from modules.inspectors.slurm_inspector import SlurmInspector
from modules.reporters.console import ConsoleReporter
from shared.exceptions import ClusterAIError
from shared.models import JobRun, RunType
from shared.utils import AUTHOR_INFO

BASE = "#!/bin/bash\n#SBATCH -J sim\n#SBATCH -p short\n#SBATCH -t 01:00:00\n#SBATCH -n 8\nsrun ./a.out\n"
OPT = "#!/bin/bash\n#SBATCH -J sim\n#SBATCH -p short\n#SBATCH -t 00:30:00\n#SBATCH -n 16\nsrun ./a.out\n"


def completed(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def make_run(job_id, run_type):
    return JobRun(job_id=job_id, user="ana", script_name="sim", script_hash="", run_type=run_type,
                  partition="short", nodes=1, cores=8, walltime_seconds=100, cpu_time_seconds=80,
                  cpu_efficiency_pct=80, max_rss_bytes=1024, core_hours=0.2)


@pytest.fixture
def console():
    return Console(record=True, width=200, color_system=None)


@pytest.fixture
def runner(tmp_path, monkeypatch, console):
    monkeypatch.delenv("CLUSTER_AI_MODE", raising=False)
    return EngineRunner(storage=StorageEngine(db_path=str(tmp_path / "t.db")),
                        console_reporter=ConsoleReporter(console=console))


@pytest.fixture
def scripts(tmp_path):
    base = tmp_path / "sim.sh"
    base.write_text(BASE, encoding="utf-8")
    opt = tmp_path / "sim.optimized.sh"
    opt.write_text(OPT, encoding="utf-8")
    return base, opt


class FakeSlurm:
    """Responde a sbatch con IDs consecutivos y a sacct con una secuencia de estados."""

    def __init__(self, sacct_outputs, sbatch_ids=(5001, 5002), reject_second=False):
        self.sacct_outputs = list(sacct_outputs)
        self.sbatch_ids = list(sbatch_ids)
        self.reject_second = reject_second
        self.sbatch_calls = []
        self.sacct_calls = 0

    def __call__(self, cmd, **kwargs):
        if cmd[0] == "sbatch":
            self.sbatch_calls.append(cmd)
            if self.reject_second and len(self.sbatch_calls) == 2:
                return completed("", "sbatch: error: Requested node configuration is not available", 1)
            return completed(f"Submitted batch job {self.sbatch_ids.pop(0)}\n")
        if cmd[0] == "sacct":
            self.sacct_calls += 1
            out = self.sacct_outputs.pop(0) if len(self.sacct_outputs) > 1 else self.sacct_outputs[0]
            return completed(out)
        raise AssertionError(f"Comando inesperado: {cmd}")


def run_benchmark(runner, fake, *args, **kwargs):
    with patch.object(SlurmInspector, "_is_slurm_available", return_value=True), \
         patch("subprocess.run", side_effect=fake), \
         patch.object(EngineRunner, "sync_job", side_effect=lambda jid, force_mock=False: make_run(jid, RunType.BASELINE)) as sync, \
         patch.object(EngineRunner, "compare", return_value="comparativa") as compare:
        result = runner.benchmark(*args, poll_seconds=0, **kwargs)
    return result, sync, compare


def test_benchmark_submits_both_waits_and_compares(runner, scripts):
    base, opt = scripts
    fake = FakeSlurm([
        "5001|PENDING\n",                          # el optimizado todavía no figura en sacct
        "5001|RUNNING\n5002|RUNNING\n",
        "5001|COMPLETED\n5002|COMPLETED\n",
    ])
    result, sync, compare = run_benchmark(runner, fake, str(base))

    assert result == "comparativa"
    assert [c[-1] for c in fake.sbatch_calls] == [str(base), str(opt)]   # usa el .optimized.sh de diagnose
    assert all("--wait" not in c for c in fake.sbatch_calls)             # se envían sin bloquear: corren a la vez
    assert fake.sacct_calls == 3
    assert [c.args[0] for c in sync.call_args_list] == [5001, 5002]
    compare.assert_called_once_with(5001, 5002, force_mock=False)


def test_benchmark_no_wait_only_submits(runner, scripts, console):
    base, _ = scripts
    fake = FakeSlurm(["unused"])
    result, sync, compare = run_benchmark(runner, fake, str(base), wait=False)

    assert result is None
    assert len(fake.sbatch_calls) == 2 and fake.sacct_calls == 0
    sync.assert_not_called()
    compare.assert_not_called()
    text = console.export_text()
    assert "cluster-ai compare 5001 5002" in text
    assert AUTHOR_INFO in text


def test_benchmark_accepts_explicit_optimized_script(runner, scripts, tmp_path):
    base, _ = scripts
    other = tmp_path / "otra_version.sh"
    other.write_text(OPT, encoding="utf-8")
    fake = FakeSlurm(["5001|COMPLETED\n5002|COMPLETED\n"])
    run_benchmark(runner, fake, str(base), optimized_path=str(other))
    assert fake.sbatch_calls[1][-1] == str(other)


def test_benchmark_skips_comparison_when_a_job_failed(runner, scripts, console):
    """Con un job fallido no se muestra una tabla engañosa: se indica qué log revisar."""
    base, opt = scripts
    fake = FakeSlurm(["5001|COMPLETED\n5002|CANCELLED by 1001\n"])
    result, sync, compare = run_benchmark(runner, fake, str(base))

    assert result is None
    sync.assert_not_called()
    compare.assert_not_called()
    text = console.export_text()
    assert "No se muestra la comparativa" in text
    assert "sim.optimized.sh → Job 5002: CANCELLED" in text
    assert "slurm-5002.out" in text                      # sin -o, el log por defecto de Slurm
    assert "Job 5001" not in text                        # el que terminó bien no se lista
    assert AUTHOR_INFO in text


def test_benchmark_failed_jobs_point_to_script_log_files(runner, tmp_path, console):
    base = tmp_path / "job.mpi"
    base.write_text("#!/bin/bash\n#SBATCH -J test\n#SBATCH -o job.%j.out\n#SBATCH -e %x.%J.err\n#SBATCH -t 00:30:00\nsrun ./a.out\n",
                    encoding="utf-8")
    (tmp_path / "job.optimized.mpi").write_text(base.read_text(encoding="utf-8"), encoding="utf-8")

    fake = FakeSlurm(["4296|FAILED\n4297|FAILED\n"], sbatch_ids=(4296, 4297))
    run_benchmark(runner, fake, str(base))
    text = console.export_text()
    assert "Job 4296: FAILED. Revise: job.4296.out, test.4296.err" in text

    console.clear()
    fake = FakeSlurm(["4298|OUT_OF_MEMORY\n4299|COMPLETED\n"], sbatch_ids=(4298, 4299))
    run_benchmark(runner, fake, str(base), duration_str="5")   # en prueba corta el nombre es <stem>-shortrun
    assert "Job 4298: OUT_OF_MEMORY. Revise: job.4298.out, job-shortrun.4298.err" in console.export_text()


def test_benchmark_requires_optimized_script(runner, tmp_path):
    base = tmp_path / "solo.sh"
    base.write_text(BASE, encoding="utf-8")
    with pytest.raises(ClusterAIError, match="cluster-ai diagnose solo.sh"):
        runner.benchmark(str(base))


def test_benchmark_rejects_same_file(runner, scripts):
    base, _ = scripts
    with pytest.raises(ClusterAIError, match="mismo archivo"):
        runner.benchmark(str(base), optimized_path=str(base))


def test_benchmark_reports_queued_baseline_when_optimized_is_rejected(runner, scripts):
    base, _ = scripts
    fake = FakeSlurm(["unused"], reject_second=True)
    with pytest.raises(ClusterAIError, match="scancel 5001"):
        run_benchmark(runner, fake, str(base))


def test_benchmark_ctrl_c_stops_waiting_but_keeps_jobs(runner, scripts, console):
    base, _ = scripts
    fake = FakeSlurm(["5001|RUNNING\n5002|RUNNING\n"])
    with patch.object(EngineRunner, "_wait_for_jobs", side_effect=KeyboardInterrupt):
        result, _, compare = run_benchmark(runner, fake, str(base))
    assert result is None
    compare.assert_not_called()
    assert "cluster-ai compare 5001 5002" in console.export_text()


def test_get_job_states_parses_sacct():
    inspector = SlurmInspector(allow_mock_fallback=False)
    out = "5001|COMPLETED\n5002|CANCELLED by 1001\n5003|\n"
    with patch("subprocess.run", return_value=completed(out)) as run_mock:
        states = inspector.get_job_states([5001, 5002, 5003])
    assert states == {5001: "COMPLETED", 5002: "CANCELLED"}   # sin estado = todavía no figura
    cmd = run_mock.call_args[0][0]
    assert cmd[cmd.index("-j") + 1] == "5001,5002,5003"


def test_benchmark_end_to_end_with_mock(tmp_path, monkeypatch, scripts, console):
    """Con el emulador, el optimizado sintético mejora y la comparativa lo refleja."""
    monkeypatch.setenv("CLUSTER_AI_MOCK_DB_PATH", str(tmp_path / "sim.db"))
    base, _ = scripts
    runner = EngineRunner(storage=StorageEngine(db_path=str(tmp_path / "t.db")),
                          console_reporter=ConsoleReporter(console=console))
    comp = runner.benchmark(str(base), force_mock=True, poll_seconds=0)

    assert comp is not None
    assert comp.baseline_run.run_type == RunType.BASELINE
    assert comp.optimized_run.run_type == RunType.OPTIMIZED
    assert comp.speedup > 1
    assert "Comparativa" in console.export_text()


def test_cli_benchmark_is_listed_and_reports_errors(tmp_path):
    cli = CliRunner()
    help_out = cli.invoke(app, ["--help"]).stdout
    assert "benchmark" in help_out

    result = cli.invoke(app, ["benchmark", str(tmp_path / "no_existe.sh")])
    assert result.exit_code == 1
    assert "Error en el benchmark" in result.stdout


# --- Corrida de prueba acotada (benchmark -d) ---------------------------------------------

def test_benchmark_duration_submits_time_limited_test_runs(runner, scripts, console):
    base, opt = scripts
    fake = FakeSlurm(["5001|TIMEOUT\n5002|TIMEOUT\n"])
    result, _, compare = run_benchmark(runner, fake, str(base), duration_str="10")

    assert result == "comparativa"
    for cmd, path in zip(fake.sbatch_calls, (base, opt)):
        assert cmd[cmd.index("--time") + 1] == "00:10:00"
        assert cmd[cmd.index("--partition") + 1] == "short"          # la cola más corta que admite 10 min
        assert cmd[cmd.index("--job-name") + 1] == f"{path.stem}-shortrun"
        assert cmd[-1] == str(path)
    text = console.export_text()
    assert "Corrida de prueba" in text
    assert "no todos los trabajos terminaron" not in text            # TIMEOUT es lo esperado aquí
    compare.assert_called_once_with(5001, 5002, force_mock=False)


def test_benchmark_without_duration_warns_on_timeout(runner, scripts, console):
    base, _ = scripts
    fake = FakeSlurm(["5001|COMPLETED\n5002|TIMEOUT\n"])
    result, _, compare = run_benchmark(runner, fake, str(base))
    assert result == "comparativa"                       # TIMEOUT sí se compara (con "no aplica")
    assert "Job(s) 5002 alcanzaron el tiempo límite de su script" in console.export_text()


@pytest.mark.parametrize("duration", ["abc", "0", "8-00:00:00"])
def test_benchmark_rejects_invalid_duration_before_submitting(runner, scripts, duration):
    base, _ = scripts
    fake = FakeSlurm(["unused"])
    with pytest.raises(ClusterAIError):
        run_benchmark(runner, fake, str(base), duration_str=duration)
    assert fake.sbatch_calls == []


def stored_pair(runner):
    storage = runner._get_storage()
    storage.save_run(make_run(1, RunType.BASELINE))
    opt = make_run(2, RunType.OPTIMIZED)
    opt.cpu_efficiency_pct = 95
    storage.save_run(opt)


def test_compare_flags_time_limited_runs(runner, console):
    stored_pair(runner)
    with patch.object(SlurmInspector, "get_job_states", return_value={1: "TIMEOUT", 2: "TIMEOUT"}):
        comp = runner.compare(1, 2)
    assert comp.time_limited
    text = console.export_text()
    assert "no aplica (corrida acotada)" in text
    assert "Speedup" not in text
    assert "+15.0%" in text                                      # la eficiencia de CPU sí se compara


def test_compare_full_runs_keep_speedup(runner, console):
    stored_pair(runner)
    with patch.object(SlurmInspector, "get_job_states", return_value={1: "COMPLETED", 2: "COMPLETED"}):
        comp = runner.compare(1, 2)
    assert not comp.time_limited
    assert "Speedup" in console.export_text()


def test_compare_without_sacct_shows_full_table(runner, console):
    from shared.exceptions import SlurmCommandError
    stored_pair(runner)
    with patch.object(SlurmInspector, "get_job_states", side_effect=SlurmCommandError("sin sacct")):
        comp = runner.compare(1, 2)
    assert not comp.time_limited
