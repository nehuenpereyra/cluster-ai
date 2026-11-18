"""tests/test_sbatch_linter.py - Parser #SBATCH fiel a Slurm y correcciones seguras del linter."""

import pytest

from modules.linters.sbatch_linter import SbatchLinter
from shared.models import LintSeverity
from shared.sbatch import parse_sbatch
from shared.utils import SBATCH_TIME_UNLIMITED, parse_sbatch_time_limit

# Script real de un investigador en el servidor (head001)
JOB_MPI = """#!/bin/bash

#SBATCH -J test               # Job name
#SBATCH -o job.%j.out         # Name of stdout output file (%j expands to jobId)
#SBATCH -N 4                 # Total number of nodes requested
#SBATCH -n 8                 # Total number of mpi tasks requested
#SBATCH -t 00:30:00           # Run time (hh:mm:ss) - 1.5 hours
##SBATCH --nodelist node004
#SBATCH --partition express

# Launch MPI-based executable
#module load mpi/mpich-x86_64
#srun /usr/lib64/mpich/bin/mpiexec -hosts node001,node004 ./a.out
srun ./a.out
#srun python3 test.py
"""


@pytest.fixture
def linter():
    return SbatchLinter()


def rules(issues):
    return {(i.rule_id, i.severity) for i in issues}


# --- Tiempo con semántica de Slurm ---------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("30", 30 * 60),                 # un número solo son minutos
    ("2:30", 2 * 60 + 30),           # M:S
    ("01:00:00", 3600),
    ("1-12", 86400 + 12 * 3600),     # D-H
    ("2-00:30", 2 * 86400 + 30 * 60),
    ("7-00:00:00", 7 * 86400),
    ("UNLIMITED", SBATCH_TIME_UNLIMITED),
    ("1.5h", None),
])
def test_parse_sbatch_time_limit(value, expected):
    assert parse_sbatch_time_limit(value) == expected


# --- Parser ----------------------------------------------------------------------------

def test_parser_handles_all_option_forms():
    script = parse_sbatch(
        "#!/bin/bash\n"
        "#SBATCH --partition express\n"
        "#SBATCH --time=2-00:00:00\n"
        "#SBATCH -pshort\n"
        "#SBATCH --mem-per-cpu=4G\n"
        "#SBATCH --exclusive\n"
        "#SBATCH --nodes 3\n"
        "#SBATCH --ntasks-per-n 2   # abreviatura única de --ntasks-per-node\n"
        "#SBATCH -o logs/run.out # comentario con -p falso\n"
        "##SBATCH -p long\n"
        "echo hola\n"
    )
    assert script.value("partition") == "short"          # la última gana
    assert [d.value for d in script.directives if d.name == "partition"] == ["express", "short"]
    assert script.value("time") == "2-00:00:00"
    assert script.value("mem-per-cpu") == "4G"
    assert script.get("exclusive") is not None and script.value("exclusive") is None
    assert script.value("nodes") == "3"
    assert script.value("ntasks-per-node") == "2"
    assert script.value("output") == "logs/run.out"
    assert script.ignored == []


def test_parser_detects_directives_after_commands():
    script = parse_sbatch("#!/bin/bash\n#SBATCH -p short\nmodule load x\n#SBATCH -t 01:00:00\n")
    assert script.value("time") is None
    assert [d.name for d in script.ignored] == ["time"]


# --- Linter sobre el script real -------------------------------------------------------

def test_real_job_mpi_lint(linter):
    issues = linter.lint("job.mpi", JOB_MPI)
    found = rules(issues)
    assert ("IALP-001", LintSeverity.ERROR) in found                       # 'express' no existe
    assert any("express" in i.message for i in issues)
    assert not any("artition" in i.message for i in issues)                # bug anterior
    assert not any(i.rule_id == "SLURM-003" and "Python" in i.message for i in issues)  # python comentado
    assert ("IALP-003", LintSeverity.ERROR) not in found                   # 8 tareas en 4 nodos = 2 por nodo
    assert ("MPI-001", LintSeverity.INFO) in found
    err = next(i for i in issues if i.rule_id == "SLURM-001")
    assert err.suggestion == "#SBATCH -e job.%j.err"


def test_real_job_mpi_fix_keeps_every_directive(linter):
    fixed, changes = linter.fix("job.mpi", JOB_MPI)
    script = parse_sbatch(fixed)

    assert script.ignored == []
    assert script.value("partition") == "short"
    assert "#SBATCH --partition short" in fixed
    assert script.value("job-name") == "test"
    assert script.value("output") == "job.%j.out"
    assert script.value("error") == "job.%j.err"
    assert script.value("nodes") == "4"
    assert script.value("ntasks") == "8"
    assert script.value("time") == "00:30:00"
    # No se inserta ningún comando ni se toca lo que no es directiva
    assert "module load" not in fixed.replace("#module load", "")
    assert fixed.count("srun ./a.out") == 1
    assert "##SBATCH --nodelist node004" in fixed
    assert len(changes) == 2
    assert linter.recommended_partition(fixed) == "short"


def test_fix_is_idempotent(linter):
    fixed, _ = linter.fix("job.mpi", JOB_MPI)
    fixed_again, changes = linter.fix("job.mpi", fixed)
    assert changes == []
    assert fixed_again == fixed


# --- Correcciones seguras --------------------------------------------------------------

def test_fix_moves_ignored_directives_and_rechecks_partition(linter):
    content = (
        "#!/bin/bash\n"
        "#SBATCH -p short\n"
        "#SBATCH -o out-%j.out\n"
        "#SBATCH -e out-%j.err\n"
        "module load python/3.12\n"
        "#SBATCH -t 05:00:00\n"
        "python sim.py\n"
    )
    issues = linter.lint("sim.sh", content)
    assert ("SLURM-004", LintSeverity.WARNING) in rules(issues)

    fixed, changes = linter.fix("sim.sh", content)
    script = parse_sbatch(fixed)
    assert script.ignored == []
    assert script.value("time") == "05:00:00"
    assert script.value("partition") == "medium"  # 5 h no entra en short
    lines = fixed.splitlines()
    assert lines.index("#SBATCH -t 05:00:00") < lines.index("module load python/3.12")


def test_fix_never_adds_time_limit(linter):
    fixed, _ = linter.fix("a.sh", "#!/bin/bash\n#SBATCH -p medium\nsrun ./a.out\n")
    assert parse_sbatch(fixed).value("time") is None
    assert ("SLURM-002", LintSeverity.WARNING) in rules(linter.lint("a.sh", "#!/bin/bash\nsrun ./a.out\n"))


def test_time_beyond_longest_partition_is_reported_not_changed(linter):
    content = "#!/bin/bash\n#SBATCH -p long\n#SBATCH -t 10-00:00:00\n#SBATCH -o a-%j.out\n#SBATCH -e a-%j.err\n./a.out\n"
    issues = linter.lint("a.sh", content)
    err = next(i for i in issues if i.rule_id == "IALP-002")
    assert "Ninguna cola" in err.suggestion
    fixed, changes = linter.fix("a.sh", content)
    assert "#SBATCH -p long" in fixed and changes == []


def test_partition_added_only_with_known_time(linter):
    fixed, changes = linter.fix("a.sh", "#!/bin/bash\n#SBATCH -t 02:00:00\n./a.out\n")
    assert parse_sbatch(fixed).value("partition") == "short"
    fixed, _ = linter.fix("a.sh", "#!/bin/bash\n./a.out\n")
    assert parse_sbatch(fixed).value("partition") is None


def test_unrelated_long_options_are_not_mistaken(linter):
    content = (
        "#!/bin/bash\n#SBATCH --partition=medium\n#SBATCH --time=01:00:00\n#SBATCH --mem-per-cpu=2G\n"
        "#SBATCH --exclusive\n#SBATCH --output=r-%j.out\n#SBATCH --error=r-%j.err\n./a.out\n"
    )
    issues = linter.lint("a.sh", content)
    assert not any(i.rule_id in ("IALP-001", "IALP-002", "SLURM-001") for i in issues)
    fixed, changes = linter.fix("a.sh", content)
    assert changes == [] and fixed == content


def test_python_module_detection(linter):
    base = "#!/bin/bash\n#SBATCH -p short\n#SBATCH -t 1:00:00\n#SBATCH -o a-%j.out\n#SBATCH -e a-%j.err\n"
    assert any(i.rule_id == "SLURM-003" for i in linter.lint("a.sh", base + "python3 sim.py\n"))
    assert not any(i.rule_id == "SLURM-003" for i in linter.lint("a.sh", base + "module load python/3.12\npython3 sim.py\n"))
    assert not any(i.rule_id == "SLURM-003" for i in linter.lint("a.sh", base + "conda activate astro\npython sim.py\n"))
    assert not any(i.rule_id == "SLURM-003" for i in linter.lint("a.sh", base + "#python sim.py\n./job_python.x\n"))


def test_mpirun_requires_real_module_name(linter):
    content = "#!/bin/bash\n#SBATCH -t 1:00:00\n#SBATCH -n 8\nmpirun ./a.out\n"
    issue = next(i for i in linter.lint("a.sh", content) if i.rule_id == "SLURM-003")
    assert "mpi/openmpi-x86_64" in issue.suggestion
    ok = "#!/bin/bash\n#SBATCH -t 1:00:00\n#SBATCH -n 8\nmodule load mpi/mpich-x86_64\nmpirun ./a.out\n"
    assert not any(i.rule_id in ("SLURM-003", "MPI-001") for i in linter.lint("a.sh", ok))


def test_cores_per_node_with_cpus_per_task(linter):
    content = "#!/bin/bash\n#SBATCH -t 1:00:00\n#SBATCH -N 1\n#SBATCH -n 4\n#SBATCH -c 8\n./a.out\n"
    assert ("IALP-003", LintSeverity.ERROR) not in rules(linter.lint("a.sh", content))  # 32 CPUs: cabe en node001-002
    content = "#!/bin/bash\n#SBATCH -t 1:00:00\n#SBATCH -N 1\n#SBATCH -n 5\n#SBATCH -c 8\n./a.out\n"
    assert ("IALP-003", LintSeverity.ERROR) in rules(linter.lint("a.sh", content))  # 40 CPUs por nodo
    content = "#!/bin/bash\n#SBATCH -t 1:00:00\n#SBATCH -N 2\n#SBATCH -n 4\n#SBATCH -c 4\n./a.out\n"
    assert not any(i.rule_id == "IALP-003" for i in linter.lint("a.sh", content))  # 8 por nodo


# --- Hardware real y cola restringida ---------------------------------------------------

def test_policy_reflects_real_nodes(linter):
    p = linter.policy
    assert (p.max_cpus_per_node, p.min_cpus_per_node) == (32, 24)
    assert (p.max_cores_per_node, p.min_cores_per_node) == (16, 12)
    assert p.large_nodes == ["node001", "node002"]
    assert "prolonged" in p.restricted


def test_restricted_partition_is_accepted_and_never_changed(linter):
    content = ("#!/bin/bash\n#SBATCH -p prolonged\n#SBATCH -t 60-00:00:00\n"
               "#SBATCH -o lo-%j.out\n#SBATCH -e lo-%j.err\n./sim\n")
    issues = linter.lint("lo.sh", content)
    assert not any(i.severity == LintSeverity.ERROR for i in issues)
    assert any(i.rule_id == "IALP-001" and "restringida" in i.message for i in issues)
    fixed, changes = linter.fix("lo.sh", content)
    assert changes == [] and "#SBATCH -p prolonged" in fixed


def test_restricted_partition_is_never_proposed(linter):
    # 10 días con -p long: no se cambia sola a prolonged, solo se menciona
    content = "#!/bin/bash\n#SBATCH -p long\n#SBATCH -t 10-00:00:00\n#SBATCH -o a-%j.out\n#SBATCH -e a-%j.err\n./a.out\n"
    err = next(i for i in linter.lint("a.sh", content) if i.rule_id == "IALP-002")
    assert "prolonged" in err.suggestion and "restringida" in err.suggestion
    assert linter.policy.smallest_fitting(10 * 86400) is None
    # Una cola inexistente nunca se reemplaza por la restringida
    fixed, _ = linter.fix("a.sh", content.replace("-p long", "-p express"))
    assert "prolonged" not in fixed


def test_smt_note_between_physical_cores_and_slurm_cpus(linter):
    content = "#!/bin/bash\n#SBATCH -t 1:00:00\n#SBATCH -N 1\n#SBATCH -n 20\n./a.out\n"
    found = rules(linter.lint("a.sh", content))
    assert ("IALP-005", LintSeverity.INFO) in found     # 20 > 16 núcleos físicos
    assert ("IALP-003", LintSeverity.ERROR) not in found
