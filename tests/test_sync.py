"""tests/test_sync.py - Pruebas unitarias para la sincronización de telemetría desde Slurm a cluster.db."""

from unittest.mock import MagicMock, patch
import pytest
from modules.engine.runner import EngineRunner
from modules.engine.storage import StorageEngine
from modules.inspectors.slurm_inspector import SlurmInspector
from shared.models import JobRun, RunType


def test_inspector_get_job_accounting_mock():
    """Verifica la extracción de telemetría contable en modo mock."""
    inspector = SlurmInspector(force_mock=True)
    # Job 1 existe en mock_jobs
    job = inspector.get_job_accounting(1)
    assert job is not None
    assert job.job_id == 1
    assert job.partition in ["short", "medium"]
    assert job.cpu_efficiency_pct > 0


@patch("subprocess.run")
def test_inspector_get_job_accounting_real_slurm(mock_run):
    """Verifica el parseo de sacct y seff desde la salida estándar de Slurm."""
    # Salida estándar simulada de `sacct -j 9999 --parsable2 --noheader ...`
    mock_sacct_output = (
        "9999|investigador|simulacion_nbody.sh|medium|1|16|7200|110000||0:0|COMPLETED\n"
        "9999.batch|investigador|batch||1|16|7200|110000|2097152K|0:0|COMPLETED\n"
    )
    # Salida estándar simulada de `seff 9999`
    mock_seff_output = (
        "Job ID: 9999\n"
        "CPU Efficiency: 95.48% of 1-08:00:00 core-walltime\n"
        "Memory Utilized: 2.00 GB\n"
    )

    def side_effect(cmd, **kwargs):
        res = MagicMock()
        res.returncode = 0
        if cmd[0] == "sacct":
            res.stdout = mock_sacct_output
        elif cmd[0] == "seff":
            res.stdout = mock_seff_output
        return res

    mock_run.side_effect = side_effect

    inspector = SlurmInspector(force_mock=False)
    # Forzamos use_mock a False para testear la ruta de Slurm real
    inspector.use_mock = False

    job = inspector.get_job_accounting(9999)

    assert job is not None
    assert job.job_id == 9999
    assert job.user == "investigador"
    assert job.script_name == "simulacion_nbody.sh"
    assert job.partition == "medium"
    assert job.cores == 16
    assert job.walltime_seconds == 7200.0
    assert job.cpu_time_seconds == 110000.0
    assert job.cpu_efficiency_pct == 95.48
    # 2097152K = 2097152 * 1024 = 2147483648 bytes (2 GB)
    assert job.max_rss_bytes == 2097152 * 1024
    assert job.core_hours == round((16 * 7200) / 3600.0, 4)


def test_runner_sync_job(tmp_path):
    """Verifica que runner.sync_job guarde el registro en la base de datos."""
    db_file = tmp_path / "test_sync.db"
    storage = StorageEngine(db_path=db_file)
    runner = EngineRunner(storage=storage)

    # Inicialmente el Job 1 no está en la base temporal
    assert storage.get_run(1) is None

    # Sincronizamos el Job 1 en modo mock
    synced_job = runner.sync_job(1, force_mock=True)
    assert synced_job.job_id == 1

    # Ahora sí debe estar persistido en la base
    stored = storage.get_run(1)
    assert stored is not None
    assert stored.job_id == 1


def test_runner_compare_auto_sync(tmp_path):
    """Verifica que runner.compare auto-sincronice los jobs si no existen en la base local."""
    db_file = tmp_path / "test_compare_sync.db"
    storage = StorageEngine(db_path=db_file)
    runner = EngineRunner(storage=storage)

    # Ambos jobs (1 y 2) están en el simulador pero no en la base local
    assert storage.get_run(1) is None
    assert storage.get_run(2) is None

    # compare debe auto-sincronizarlos sin fallar
    comparison = runner.compare(1, 2, force_mock=True)
    assert comparison is not None
    assert comparison.baseline_run.job_id == 1
    assert comparison.optimized_run.job_id == 2

    # Verificamos que quedaron guardados
    assert storage.get_run(1) is not None
    assert storage.get_run(2) is not None
