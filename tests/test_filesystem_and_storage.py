"""tests/test_filesystem_and_storage.py - Pruebas de copias de seguridad inmutables y persistencia SQLite."""

import pytest
from pathlib import Path
from shared.filesystem import create_immutable_backup, write_optimized_script, generate_diff
from modules.engine.storage import StorageEngine
from shared.models import JobRun, RunType

def test_create_immutable_backup(tmp_path):
    orig = tmp_path / "original.sh"
    orig.write_text("#!/bin/bash\necho 1", encoding="utf-8")

    backup = create_immutable_backup(orig)
    assert backup.is_file()
    assert backup.name.startswith("original.sh.bak_")
    assert backup.read_text(encoding="utf-8") == "#!/bin/bash\necho 1"
    # El archivo original sigue existiendo
    assert orig.is_file()

def test_write_optimized_script(tmp_path):
    orig = tmp_path / "job.sh"
    orig.write_text("#!/bin/bash\n#SBATCH -p short\n", encoding="utf-8")

    opt = write_optimized_script(orig, "#!/bin/bash\n#SBATCH -p medium\n")
    assert opt.name == "job.optimized.sh"
    assert opt.read_text(encoding="utf-8") == "#!/bin/bash\n#SBATCH -p medium\n"
    # Original no modificado
    assert "short" in orig.read_text(encoding="utf-8")

def test_storage_compare_runs(tmp_path):
    db_file = tmp_path / "storage_test.db"
    storage = StorageEngine(db_path=str(db_file))

    r1 = JobRun(
        job_id=101, user="astro", script_name="run.sh", script_hash="hash1",
        run_type=RunType.BASELINE, partition="medium", nodes=1, cores=16,
        walltime_seconds=3600.0, cpu_time_seconds=1800.0, cpu_efficiency_pct=50.0,
        max_rss_bytes=1024*1024*1024*8, core_hours=16.0
    )
    r2 = JobRun(
        job_id=102, user="astro", script_name="run.optimized.sh", script_hash="hash2",
        run_type=RunType.OPTIMIZED, partition="medium", nodes=1, cores=16,
        walltime_seconds=1800.0, cpu_time_seconds=1620.0, cpu_efficiency_pct=90.0,
        max_rss_bytes=1024*1024*1024*4, core_hours=8.0
    )

    storage.save_run(r1)
    storage.save_run(r2)

    comp = storage.compare_runs(101, 102)
    assert comp.speedup == 2.0
    assert comp.walltime_saved_seconds == 1800.0
    assert comp.cpu_efficiency_gain_pct == 40.0
    assert comp.core_hours_saved == 8.0

def test_mock_db_isolation(monkeypatch, tmp_path):
    from modules.engine.storage import resolve_db_path, StorageEngine
    
    prod_db = tmp_path / "prod.db"
    mock_db = tmp_path / "mock.db"
    monkeypatch.setenv("CLUSTER_AI_MODE", "cluster")
    monkeypatch.setenv("CLUSTER_AI_DB_PATH", str(prod_db))
    monkeypatch.setenv("CLUSTER_AI_MOCK_DB_PATH", str(mock_db))

    # Con force_mock=False
    p_prod = resolve_db_path(force_mock=False)
    assert p_prod == prod_db

    # Con force_mock=True
    p_mock = resolve_db_path(force_mock=True)
    assert p_mock == mock_db

    # Escribir en mock_storage no debe crear ni modificar prod_db
    mock_storage = StorageEngine(force_mock=True)
    r = JobRun(
        job_id=999, user="tester", script_name="test.sh", script_hash="h999",
        run_type=RunType.SHORT_RUN, partition="short", nodes=1, cores=1,
        walltime_seconds=10.0, cpu_time_seconds=8.0, cpu_efficiency_pct=80.0,
        max_rss_bytes=1000, core_hours=0.01
    )
    mock_storage.save_run(r)

    assert mock_db.is_file()
    assert not prod_db.is_file()

