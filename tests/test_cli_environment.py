"""tests/test_cli_environment.py - En producción no se ofrecen el emulador ni las opciones --mock."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from shared import features

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_cli(env_value, *args):
    env = dict(os.environ, CLUSTER_AI_ENV=env_value, COLUMNS="200", PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "modules.cli.main", *args],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
    )


@pytest.mark.parametrize("value, expected", [
    ("production", True), ("produccion", True), ("PROD", True),
    ("development", False), ("dev", False),
])
def test_is_production_from_env(monkeypatch, value, expected):
    monkeypatch.setenv("CLUSTER_AI_ENV", value)
    assert features.is_production() is expected


def test_is_production_autodetects_installed_helper(monkeypatch, tmp_path):
    monkeypatch.delenv("CLUSTER_AI_ENV", raising=False)
    helper = tmp_path / "cluster-ai-priv"
    monkeypatch.setattr(features, "DEFAULT_HELPER_PATH", str(helper))
    monkeypatch.setattr(features.os, "name", "posix")
    assert features.is_production() is False
    helper.write_text("#!/bin/bash\n")
    assert features.is_production() is True


def test_production_help_hides_mock():
    result = run_cli("production", "--help")
    assert result.returncode == 0
    assert "--mock" not in result.stdout
    assert "mock " not in result.stdout.split("Commands")[-1]
    assert "Slurm-Mockup" not in result.stdout
    for command in ("diagnose", "run", "short-run", "compare", "sync", "history"):
        assert command in result.stdout


def test_production_rejects_mock_command_and_flag():
    assert "No such command" in run_cli("production", "mock", "sinfo").stderr
    result = run_cli("production", "history", "--mock")
    assert result.returncode == 2
    assert "no está disponible en el servidor de producción" in result.stdout
    options = run_cli("production", "short-run", "--help").stdout
    assert "--mock" not in options and "--real" not in options


def test_development_help_shows_mock():
    result = run_cli("development", "--help")
    assert result.returncode == 0
    assert "--mock" in result.stdout and "Slurm-Mockup" in result.stdout
