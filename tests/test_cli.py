"""The CLI is how most people will meet the project."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from earthguardian.cli import app

runner = CliRunner()


def test_help_lists_every_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("simulate", "analyze", "plan", "impact", "dashboard", "demo", "info"):
        assert command in result.output


def test_info_runs_and_shows_the_fleet():
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    assert "SP-IBIUNA-01" in result.output


@pytest.mark.parametrize("command", ["simulate", "analyze", "plan", "impact"])
def test_subcommand_help(command):
    assert runner.invoke(app, [command, "--help"]).exit_code == 0


def test_simulate_rejects_an_unknown_policy():
    result = runner.invoke(app, ["simulate", "--policy", "wishful"])
    assert result.exit_code == 1
    assert "Unknown policy" in result.output
