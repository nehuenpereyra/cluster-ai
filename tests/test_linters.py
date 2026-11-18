"""tests/test_linters.py - Pruebas para linters determinísticos (sbatch, python, mpi)."""

from modules.linters.sbatch_linter import SbatchLinter
from modules.linters.python_linter import PythonLinter
from modules.linters.mpi_linter import MPILinter
from shared.models import LintSeverity

def test_sbatch_invalid_partition():
    linter = SbatchLinter()
    content = """#!/bin/bash
#SBATCH -J test
#SBATCH -p particion_inexistente
#SBATCH -t 01:00:00
"""
    issues = linter.lint("test.sh", content)
    assert any(i.rule_id == "IALP-001" and i.severity == LintSeverity.ERROR for i in issues)

def test_sbatch_time_exceeds_short_partition():
    linter = SbatchLinter()
    content = """#!/bin/bash
#SBATCH -J test
#SBATCH -p short
#SBATCH -t 05:00:00
#SBATCH -o out-%j.out
#SBATCH -e err-%j.err
"""
    issues = linter.lint("test.sh", content)
    # 5h > 4h de short
    assert any(i.rule_id == "IALP-002" and i.severity == LintSeverity.ERROR for i in issues)

def test_sbatch_cores_exceed_ryzen_max():
    linter = SbatchLinter()
    content = """#!/bin/bash
#SBATCH -J test
#SBATCH -p medium
#SBATCH -t 01:00:00
#SBATCH -N 1
#SBATCH -n 40
"""
    issues = linter.lint("test.sh", content)
    # 40 CPUs en 1 nodo > 32 CPUs de Slurm del 5950X (16 núcleos con SMT)
    assert any(i.rule_id == "IALP-003" and i.severity == LintSeverity.ERROR for i in issues)

def test_sbatch_32_tasks_fit_only_large_nodes_with_smt():
    linter = SbatchLinter()
    content = "#!/bin/bash\n#SBATCH -p medium\n#SBATCH -t 01:00:00\n#SBATCH -N 1\n#SBATCH -n 32\n"
    issues = linter.lint("test.sh", content)
    assert not any(i.rule_id == "IALP-003" and i.severity == LintSeverity.ERROR for i in issues)
    info = next(i for i in issues if i.rule_id == "IALP-003")
    assert "node001" in info.message and "node002" in info.message
    assert any(i.rule_id == "IALP-005" for i in issues)  # supera los 16 núcleos físicos: SMT

def test_sbatch_fix_auto_adjustment():
    linter = SbatchLinter()
    content = """#!/bin/bash
#SBATCH -J test
#SBATCH -p short
#SBATCH -t 08:00:00
#SBATCH -N 1
#SBATCH -n 24
#SBATCH -o log.txt
#SBATCH -e err.txt
python3 test.py
"""
    fixed_content, changes = linter.fix("test.sh", content)
    # Cola ajustada a la más corta que admite 8 horas
    assert "#SBATCH -p medium" in fixed_content
    # Los logs conservan su nombre, con el JobID agregado
    assert "#SBATCH -o log-%j.txt" in fixed_content
    assert "#SBATCH -e err-%j.txt" in fixed_content
    # Cambios inseguros que NO se hacen automáticamente (quedan como recomendación)
    assert "#SBATCH -n 24" in fixed_content
    assert "module load" not in fixed_content
    assert len(changes) == 3
    issues = linter.lint("test.sh", content)
    # 24 CPUs en 1 nodo caben en cualquier nodo (24 o 32 CPUs), pero superan los núcleos físicos: SMT
    assert not any(i.rule_id == "IALP-003" and i.severity == LintSeverity.ERROR for i in issues)
    assert any(i.rule_id == "IALP-005" for i in issues)
    assert any(i.rule_id == "SLURM-003" and i.suggestion == "module load python/3.12" for i in issues)

def test_python_linter_nested_loops_and_io():
    linter = PythonLinter()
    code = """
for i in range(100):
    for j in range(100):
        with open("file.txt", "w") as f:
            f.write(str(i+j))
"""
    issues = linter.lint("sim.py", code)
    assert any(i.rule_id == "PY-001" for i in issues)
    assert any(i.rule_id == "PY-003" for i in issues)
