"""FabricMaterializerAgent — the 6th Fabric agent.

Unlike the five Q&A agents that wrap :class:`FabricDataAgentClient` and ask a
Foundry agent questions, this agent **executes** work on the Fabric side. It
submits a deterministic PySpark payload to the per-lakehouse Livy Batch API
and waits for a terminal state.

Design boundary
---------------
* The agent is a thin orchestration boundary — no LLM, no Foundry function
  tool, no schema negotiation. Everything it needs is deterministic:
  the table list, the view SQL, and the workspace/lakehouse GUIDs from
  :mod:`regimpact.settings`.
* Livy failures raise :class:`FabricMaterializerError`. This is the
  constitutional guardrail — the CLI decides whether to keep going after a
  materialize failure, but the agent itself never silently succeeds.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .fabric_livy_client import (
    FabricLivyClient,
    LivyError,
    LivyNotConfiguredError,
)
from .fabric_materializer_spec import MATERIALIZE_PYSPARK_TEMPLATE

logger = logging.getLogger(__name__)


class FabricMaterializerError(Exception):
    """Raised when the materialize batch fails, times out, or is misconfigured."""


class FabricMaterializerAgent:
    """The 6th Fabric agent — materializes uploaded parquet into Delta + views.

    Parameters
    ----------
    livy_client:
        Optional pre-built :class:`FabricLivyClient`. When omitted, one is
        constructed from :mod:`regimpact.settings` at :meth:`materialize` time
        so tests and callers with in-hand credentials can inject their own.
    """

    name = "Lakehouse Materializer"

    def __init__(self, livy_client: FabricLivyClient | None = None) -> None:
        self._livy_client = livy_client

    def materialize(
        self,
        *,
        workspace_id: str | None = None,
        lakehouse_id: str | None = None,
        credential: Any = None,
        timeout_seconds: int | None = None,
        poll_interval_seconds: int = 5,
    ) -> dict[str, Any]:
        """Submit the materialize batch and wait for it to reach ``success``.

        Returns
        -------
        dict
            ``{"batch_id": str, "state": "success",
              "duration_seconds": float, "final_payload": dict}``.

        Raises
        ------
        FabricMaterializerError
            The client is misconfigured, the batch failed, or the timeout
            elapsed. The original :class:`LivyError` is chained via ``__cause__``.
        """
        client = self._resolve_client(
            workspace_id=workspace_id,
            lakehouse_id=lakehouse_id,
            credential=credential,
        )
        timeout = timeout_seconds if timeout_seconds is not None else _default_timeout()

        started = time.monotonic()
        try:
            batch_id = client.submit_batch(
                MATERIALIZE_PYSPARK_TEMPLATE,
                name="regimpact-materialize",
            )
        except LivyError as exc:
            raise FabricMaterializerError(
                f"Materialize submit failed: {exc}"
            ) from exc

        logger.info("Materialize batch submitted: id=%s", batch_id)

        try:
            payload = client.wait_for_completion(
                batch_id,
                timeout_seconds=timeout,
                poll_interval_seconds=poll_interval_seconds,
            )
        except LivyError as exc:
            raise FabricMaterializerError(
                f"Materialize batch {batch_id} did not succeed: {exc}"
            ) from exc

        duration = time.monotonic() - started
        return {
            "batch_id": batch_id,
            "state": str(payload.get("state") or "success"),
            "duration_seconds": duration,
            "final_payload": payload,
        }

    # ---------------------------------------------------------------- helpers
    def _resolve_client(
        self,
        *,
        workspace_id: str | None,
        lakehouse_id: str | None,
        credential: Any,
    ) -> FabricLivyClient:
        if self._livy_client is not None:
            return self._livy_client
        from ..settings import settings

        ws = workspace_id or settings.fabric_workspace_id
        lh = lakehouse_id or settings.fabric_lakehouse_id
        try:
            return FabricLivyClient(
                workspace_id=ws,
                lakehouse_id=lh,
                credential=credential,
            )
        except LivyNotConfiguredError as exc:
            raise FabricMaterializerError(str(exc)) from exc


def _default_timeout() -> int:
    """Read the materialize timeout from settings, defaulting to 600s."""
    try:
        from ..settings import settings

        return int(settings.fabric_materialize_timeout_seconds)
    except Exception:  # noqa: BLE001
        return 600


__all__ = ["FabricMaterializerAgent", "FabricMaterializerError"]
