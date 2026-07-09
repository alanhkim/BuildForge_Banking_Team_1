from pathlib import Path

from regimpact.fabric_smoke import load_fabric_smoke_prompts


PROMPT_PATH = Path("data/fabric_data_agent_smoke_prompts.yaml")


def test_fabric_smoke_prompts_cover_required_categories():
    prompt_set = load_fabric_smoke_prompts(PROMPT_PATH)

    assert prompt_set.foundry_agent_name == "FabricTest"
    assert prompt_set.foundry_agent_version == "2"
    assert {prompt.category for prompt in prompt_set.prompts} == {
        "evidence_health",
        "gap_blast_radius",
        "lineage",
        "obligation_control_map",
        "remediation",
        "score_story",
    }


def test_fabric_smoke_prompts_require_fabric_grounding():
    prompt_set = load_fabric_smoke_prompts(PROMPT_PATH)

    for prompt in prompt_set.prompts:
        assert "Fabric Data Agent tool" in prompt.prompt
        assert len(prompt.expected_characteristics) >= 3


def test_fabric_smoke_prompt_ids_are_unique():
    prompt_set = load_fabric_smoke_prompts(PROMPT_PATH)

    prompt_ids = [prompt.id for prompt in prompt_set.prompts]
    assert len(prompt_ids) == len(set(prompt_ids))
