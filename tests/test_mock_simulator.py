"""tests/test_mock_simulator.py - Pruebas unitarias para el emulador Slurm-Mockup."""

import pytest
from pathlib import Path
from modules.mock.simulator import SlurmSimulator
from modules.engine.storage import StorageEngine
from shared.models import RunType

@pytest.fixture
def temp_simulator(tmp_path):
    db_file = tmp_path / "test_cluster.db"
    storage = StorageEngine(db_path=str(db_file))
    sim = SlurmSimulator(storage=storage)
    return sim

def test_sinfo_returns_ialp_partitions(temp_simulator):
    info = temp_simulator.sinfo()
    partitions = [p["partition"] for p in info]
    assert "short" in partitions
    assert "medium" in partitions
    assert "long" in partitions
    assert "prolonged" in partitions  # cola restringida del servidor

    # Verificar que medium es la cola por defecto
    medium_part = next(p for p in info if p["partition"] == "medium")
    assert medium_part["is_default"] is True

def test_sbatch_and_sacct(temp_simulator, tmp_path):
    script = tmp_path / "test_job.sh"
    script.write_text("""#!/bin/bash
#SBATCH -J test_sim
#SBATCH -o out-%j.out
#SBATCH -e err-%j.err
#SBATCH -t 00:30:00
#SBATCH -p short
echo "Hello Mock"
""", encoding="utf-8")

    job_id = temp_simulator.sbatch(str(script), run_locally=False, run_type=RunType.BASELINE)
    assert job_id > 0

    acct = temp_simulator.sacct(job_id=job_id)
    assert len(acct) >= 1
    assert acct[0]["JobID"] == job_id
    assert acct[0]["Partition"] == "short"

    eff = temp_simulator.seff(job_id)
    assert eff["job_id"] == job_id
    assert "cpu_efficiency_pct" in eff

def test_scancel(temp_simulator, tmp_path):
    script = tmp_path / "cancel_job.sh"
    script.write_text("#!/bin/bash\n#SBATCH -J test\n", encoding="utf-8")
    job_id = temp_simulator.sbatch(str(script), run_locally=False)
    
    # Simular que está PENDING
    with temp_simulator.storage._get_connection() as conn:
        conn.execute("UPDATE mock_jobs SET status = 'PENDING' WHERE job_id = ?", (job_id,))

    success = temp_simulator.scancel(job_id)
    assert success is True
