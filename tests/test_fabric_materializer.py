"""Tests for :class:`regimpact.agents.fabric_materializer.FabricMaterializerAgent`.

The agent's only external dependency is a :class:`FabricLivyClient`. We inject
a stub client per-test — no HTTP, no auth, no Spark.
"""
from __future__ import annotations

from typing import Any

import pytest

from regimpact.agents.fabric_livy_client import (
    LivyExecutionError,
    LivyTimeoutError,
)
from regimpact.agents.fabric_materializer import (
    FabricMaterializerAgent,
    FabricMaterializerError,
)
from regimpact.agents.fabric_materializer_spec import (
    GOLD_TABLES,
    MATERIALIZE_PYSPARK_TEMPLATE,
    RAW_TABLES,
    VIEW_NAMES,
)


class _StubLivyClient:
    """Records ``submit_batch`` / ``wait_for_completion`` calls."""

    def __init__(
        self,
        *,
        batch_id: str = "batch-1",
        wait_return: dict[str, Any] | None = None,
        wait_raises: Exception | None = None,
    ) -> None:
        self.batch_id = batch_id
        self.wait_return = wait_return or {"state": "success", "id": batch_id}
        self.wait_raises = wait_raises
        self.submit_calls: list[dict[str, Any]] = []
        self.wait_calls: list[dict[str, Any]] = []

    def submit_batch(self, code: str, name: str = "regimpact-materialize") -> str:
        self.submit_calls.append({"code": code, "name": name})
        return self.batch_id

    def wait_for_completion(
        self,
        batch_id: str,
        timeout_seconds: int = 600,
        poll_interval_seconds: int = 5,
    ) -> dict[str, Any]:
        self.wait_calls.append(
            {
                "batch_id": batch_id,
                "timeout_seconds": timeout_seconds,
                "poll_interval_seconds": poll_interval_seconds,
            }
        )
        if self.wait_raises is not None:
            raise self.wait_raises
        return self.wait_return


# ---------------------------------------------------- happy path


def test_materialize_submits_template_and_returns_summary() -> None:
    stub = _StubLivyClient(
        batch_id="B-42",
        wait_return={"state": "success", "id": "B-42", "log": ["ok"]},
    )
    agent = FabricMaterializerAgent(livy_client=stub)

    summary = agent.materialize(timeout_seconds=15, poll_interval_seconds=1)

    assert summary["batch_id"] == "B-42"
    assert summary["state"] == "success"
    assert summary["duration_seconds"] >= 0.0
    assert summary["final_payload"]["log"] == ["ok"]

    assert len(stub.submit_calls) == 1
    assert stub.submit_calls[0]["code"] == MATERIALIZE_PYSPARK_TEMPLATE
    assert stub.submit_calls[0]["name"] == "regimpact-materialize"
    assert stub.wait_calls == [
        {"batch_id": "B-42", "timeout_seconds": 15, "poll_interval_seconds": 1}
    ]


# ---------------------------------------------------- error wrapping


def test_materialize_wraps_livy_execution_error() -> None:
    stub = _StubLivyClient(
        wait_raises=LivyExecutionError("batch dead")
    )
    agent = FabricMaterializerAgent(livy_client=stub)
    with pytest.raises(FabricMaterializerError) as excinfo:
        agent.materialize(timeout_seconds=5, poll_interval_seconds=1)
    assert "batch dead" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, LivyExecutionError)


def test_materialize_wraps_livy_timeout_error() -> None:
    stub = _StubLivyClient(wait_raises=LivyTimeoutError("no terminal state"))
    agent = FabricMaterializerAgent(livy_client=stub)
    with pytest.raises(FabricMaterializerError) as excinfo:
        agent.materialize(timeout_seconds=5, poll_interval_seconds=1)
    assert isinstance(excinfo.value.__cause__, LivyTimeoutError)


# ---------------------------------------------------- default timeout


def test_materialize_uses_settings_default_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubLivyClient()
    monkeypatch.setattr(
        "regimpact.agents.fabric_materializer._default_timeout", lambda: 777
    )
    agent = FabricMaterializerAgent(livy_client=stub)

    agent.materialize(poll_interval_seconds=1)

    assert stub.wait_calls[0]["timeout_seconds"] == 777


# ---------------------------------------------------- pyspark payload shape


def test_pyspark_template_mentions_every_raw_table() -> None:
    for name in RAW_TABLES:
        assert name in MATERIALIZE_PYSPARK_TEMPLATE, (
            f"raw table {name!r} missing from PySpark template"
        )


def test_pyspark_template_mentions_every_gold_table() -> None:
    for name in GOLD_TABLES:
        assert name in MATERIALIZE_PYSPARK_TEMPLATE, (
            f"gold table {name!r} missing from PySpark template"
        )


def test_pyspark_template_creates_all_views() -> None:
    for view in VIEW_NAMES:
        assert f"CREATE OR REPLACE VIEW {view}" in MATERIALIZE_PYSPARK_TEMPLATE, (
            f"view {view!r} not declared in PySpark template"
        )


def test_pyspark_template_uses_regimpact_subpaths() -> None:
    from regimpact.lakehouse import GOLD_SUBPATH, RAW_SUBPATH

    assert f"Files/{RAW_SUBPATH}" in MATERIALIZE_PYSPARK_TEMPLATE
    assert f"Files/{GOLD_SUBPATH}" in MATERIALIZE_PYSPARK_TEMPLATE
    # And no placeholder should leak through.
    assert "__RAW_SUBPATH__" not in MATERIALIZE_PYSPARK_TEMPLATE
    assert "__GOLD_SUBPATH__" not in MATERIALIZE_PYSPARK_TEMPLATE
    assert "__RAW_TABLES__" not in MATERIALIZE_PYSPARK_TEMPLATE
    assert "__GOLD_TABLES__" not in MATERIALIZE_PYSPARK_TEMPLATE
