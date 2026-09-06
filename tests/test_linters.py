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
#SBATCH -n 32
"""
    issues = linter.lint("test.sh", content)
    # 32 cores en 1 nodo > 16 cores del 5950X
    assert any(i.rule_id == "IALP-003" and i.severity == LintSeverity.ERROR for i in issues)

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
    assert "-p medium" in fixed_content
    assert "-n 16" in fixed_content
    assert "salida-%j.out" in fixed_content
    assert "salida-%j.err" in fixed_content
    assert "module load python3" in fixed_content
    assert len(changes) >= 4

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
