"""tests/test_cli.py - Pruebas de integración para la interfaz de consola (CLI)."""

from typer.testing import CliRunner
from modules.cli.main import app
from shared.utils import AUTHOR_INFO

runner = CliRunner()

def test_cli_version_contains_author():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "Cluster-AI" in result.stdout
    assert "0.0.1" in result.stdout
    assert AUTHOR_INFO in result.stdout

def test_cli_help_contains_author():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Cluster-AI" in result.stdout
    assert "CLUSTER GAMA (FCAG-IALP)" in result.stdout
    assert AUTHOR_INFO in result.stdout

def test_cli_mock_sinfo():
    result = runner.invoke(app, ["mock", "sinfo"])
    assert result.exit_code == 0
    assert "short" in result.stdout
    assert "medium" in result.stdout
    assert AUTHOR_INFO in result.stdout
