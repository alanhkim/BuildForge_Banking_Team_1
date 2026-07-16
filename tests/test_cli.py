from typer.testing import CliRunner

from regimpact import cli
from regimpact.agents.foundry_client import FabricDataAgentError
from regimpact.contracts import FabricQuestionResponse, SourceReference, ToolEvidence
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


def test_demo_cli_reports_foundry_fabric_first_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "settings", Settings(output_dir=tmp_path))

    result = runner.invoke(cli.app, ["demo"])

    assert result.exit_code == 0
    assert "Foundry/Fabric-first" in result.stdout
    assert "Deterministic (offline)" not in result.stdout


def test_ask_fabric_cli_prints_grounded_answer(monkeypatch):
    class FakeFabricClient:
        def ask(self, question):
            assert question == "How compliant is DORA?"
            return FabricQuestionResponse(
                question=question,
                answer="DORA moves from 54.8 AsIs to 59.6 PostRemediation.",
                agent_name="RegImpactQA",
                agent_version="1",
                citations=[
                    SourceReference(
                        source="RegImpactLH",
                        reference_type="table",
                        name="compliance_scores",
                        value="CHG-DORA",
                    )
                ],
                tool_evidence=[
                    ToolEvidence(
                        tool_name="fabric_dataagent_preview",
                        data_source="RegImpactLH",
                        query="SELECT Scenario, Score FROM compliance_scores",
                    )
                ],
                confidence="high",
            )

    monkeypatch.setattr(cli, "_fabric_data_agent_client", lambda: FakeFabricClient())

    result = runner.invoke(cli.app, ["ask-fabric", "How compliant is DORA?"])

    assert result.exit_code == 0
    assert "Fabric-grounded answer" in result.stdout
    assert "DORA moves from 54.8" in result.stdout
    assert "compliance_scores" in result.stdout
    assert "fabric_dataagent_preview" in result.stdout


def test_ask_fabric_cli_surfaces_missing_configuration(monkeypatch):
    monkeypatch.setattr(cli, "settings", Settings())

    result = runner.invoke(cli.app, ["ask-fabric", "How compliant is DORA?"])

    assert result.exit_code == 1
    assert "Fabric Data Agent failed" in result.stdout
    assert "Missing Fabric Data Agent configuration" in result.stdout


def test_ask_fabric_cli_surfaces_invocation_failure(monkeypatch):
    class FailingFabricClient:
        def ask(self, question):
            raise FabricDataAgentError("Foundry Fabric Data Agent invocation failed")

    monkeypatch.setattr(cli, "_fabric_data_agent_client", lambda: FailingFabricClient())

    result = runner.invoke(cli.app, ["ask-fabric", "How compliant is DORA?"])

    assert result.exit_code == 1
    assert "Fabric Data Agent failed" in result.stdout
    assert "invocation failed" in result.stdout


def test_interpret_cli_surfaces_missing_foundry_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "settings", Settings(output_dir=tmp_path))

    result = runner.invoke(
        cli.app,
        [
            "interpret",
            "--file",
            "data/regulations/eu_ai_act_high_risk.txt",
            "--regulation",
            "REG-AIACT",
            "--name",
            "EU AI Act",
            "--title",
            "High-risk AI update",
        ],
    )

    assert result.exit_code == 1
    assert "Foundry interpreter failed" in result.stdout
    assert "Missing Foundry configuration" in result.stdout
    assert not (tmp_path / "tables" / "obligations.parquet").exists()


def test_interpret_cli_surfaces_missing_fabric_configuration(tmp_path, monkeypatch):
    """Interpretation succeeds but pipeline fails when Fabric config is absent."""
    from regimpact.agents import pipeline as pipeline_module
    from regimpact.contracts import InterpretResponse, Obligation

    stub_response = InterpretResponse(
        regulation_id="REG-AIACT",
        change_id="CHG-AIACT-UPLOAD",
        obligations=[
            Obligation(
                id="OBL-CHG-AIACT-UPLOAD-01",
                change_id="CHG-AIACT-UPLOAD",
                theme="AI_GOVERNANCE",
                summary="Maintain human oversight of high-risk AI systems.",
                target_maturity=4,
                criticality="High",
                affected_data_domain_ids=["DD-REF"],
                source_refs=["source:eu_ai_act:1"],
            )
        ],
    )

    class StubInterpreter:
        def interpret(self, request):
            return stub_response

    # Patch the interpreter so Foundry is not called.
    monkeypatch.setattr(pipeline_module, "InterpreterAgent", lambda: StubInterpreter())
    # Patch pipeline._settings directly to remove all Fabric/Foundry config,
    # ensuring foundry_fabric_enabled returns False regardless of env vars.
    monkeypatch.setattr(
        pipeline_module,
        "_settings",
        Settings(
            output_dir=tmp_path,
            foundry_project_endpoint="",
            foundry_executive_qa_agent_name="",
            foundry_executive_qa_agent_version="",
            fabric_workspace_id="",
            fabric_data_agent_id="",
        ),
    )
    monkeypatch.setattr(cli, "settings", Settings(output_dir=tmp_path))

    result = runner.invoke(
        cli.app,
        [
            "interpret",
            "--file",
            "data/regulations/eu_ai_act_high_risk.txt",
            "--regulation",
            "REG-AIACT",
            "--name",
            "EU AI Act",
            "--title",
            "High-risk AI update",
        ],
    )

    assert result.exit_code == 1
    assert "Fabric pipeline failed" in result.stdout
    assert "Foundry/Fabric configuration is required" in result.stdout
