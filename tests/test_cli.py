from typer.testing import CliRunner

from regimpact import cli
from regimpact.settings import Settings


runner = CliRunner()


def test_list_changes_cli_shows_dora():
    result = runner.invoke(cli.app, ["list-changes"])

    assert result.exit_code == 0
    assert "CHG-DORA" in result.stdout


def test_score_cli_runs_dora_scorecard():
    result = runner.invoke(cli.app, ["score", "--change", "CHG-DORA"])

    assert result.exit_code == 0
    assert "Compliance score" in result.stdout
    assert "Post-remediation" in result.stdout


def test_generate_cli_writes_parquet_to_configured_output(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "settings", Settings(output_dir=tmp_path))

    result = runner.invoke(cli.app, ["generate"])

    assert result.exit_code == 0
    assert (tmp_path / "tables" / "regulations.parquet").exists()
    assert (tmp_path / "tables" / "relationships.parquet").exists()
