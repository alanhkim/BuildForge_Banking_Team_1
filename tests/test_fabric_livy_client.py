"""Tests for :mod:`regimpact.agents.fabric_livy_client`.

The client is deliberately structured so :meth:`FabricLivyClient._do_http` is
the single seam where HTTP happens. All tests replace it with a scripted stub
and assert on the calls it receives — no ``urllib.request``, no network.
"""
from __future__ import annotations

from typing import Any

import pytest

from regimpact.agents.fabric_livy_client import (
    FABRIC_TOKEN_SCOPE,
    FabricLivyClient,
    LivyExecutionError,
    LivyNotConfiguredError,
    LivySubmitError,
    LivyTimeoutError,
)


class _FakeToken:
    def __init__(self, token: str) -> None:
        self.token = token


class _FakeCredential:
    """Records ``get_token`` calls and returns a scripted token."""

    def __init__(self, token: str = "fake-token") -> None:
        self._token = token
        self.calls: list[str] = []

    def get_token(self, scope: str) -> _FakeToken:
        self.calls.append(scope)
        return _FakeToken(self._token)


def _make_client(
    responses: list[tuple[int, dict[str, Any]]],
    credential: _FakeCredential | None = None,
) -> tuple[FabricLivyClient, list[dict[str, Any]]]:
    """Build a client whose ``_do_http`` returns ``responses`` in order.

    Returns the client + a mutable list of every request made, so tests can
    inspect method / URL / headers / body.
    """
    calls: list[dict[str, Any]] = []
    remaining = list(responses)

    def fake_do_http(
        self: FabricLivyClient,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any] | None = None,
        timeout_seconds: float = 30.0,
    ) -> tuple[int, dict[str, Any]]:
        calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body,
            }
        )
        if not remaining:
            raise AssertionError(
                "Test ran out of scripted responses; got extra "
                f"{method} {url}"
            )
        return remaining.pop(0)

    client = FabricLivyClient(
        workspace_id="ws-1",
        lakehouse_id="lh-1",
        credential=credential or _FakeCredential(),
    )
    client._do_http = fake_do_http.__get__(client, FabricLivyClient)  # type: ignore[method-assign]
    return client, calls


# ---------------------------------------------------------- configuration


def test_client_raises_when_workspace_id_missing() -> None:
    with pytest.raises(LivyNotConfiguredError):
        FabricLivyClient(workspace_id="", lakehouse_id="lh-1", credential=object())


def test_client_raises_when_lakehouse_id_missing() -> None:
    with pytest.raises(LivyNotConfiguredError):
        FabricLivyClient(workspace_id="ws-1", lakehouse_id="", credential=object())


# ----------------------------------------------------------------- submit


def test_submit_batch_returns_batch_id_and_uses_bearer_token() -> None:
    cred = _FakeCredential("token-abc")
    client, calls = _make_client([(201, {"id": 42, "state": "starting"})], cred)

    batch_id = client.submit_batch("print('hi')", name="unit-test")

    assert batch_id == "42"
    assert cred.calls == [FABRIC_TOKEN_SCOPE]
    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/batches")
    assert call["headers"]["Authorization"] == "Bearer token-abc"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["body"] == {
        "name": "unit-test",
        "language": "python",
        "code": "print('hi')",
    }


def test_submit_batch_rejects_empty_code() -> None:
    client, _ = _make_client([])
    with pytest.raises(LivySubmitError):
        client.submit_batch("   ")


def test_submit_batch_raises_on_http_error() -> None:
    client, _ = _make_client(
        [(500, {"error": "internal-server-error"})]
    )
    with pytest.raises(LivySubmitError) as excinfo:
        client.submit_batch("print('hi')")
    assert "500" in str(excinfo.value)


def test_submit_batch_raises_when_id_missing() -> None:
    client, _ = _make_client([(200, {"state": "starting"})])
    with pytest.raises(LivySubmitError):
        client.submit_batch("print('hi')")


# ------------------------------------------------------------- polling


def test_wait_for_completion_success(monkeypatch: pytest.MonkeyPatch) -> None:
    # First poll: still starting. Second poll: success.
    client, calls = _make_client(
        [
            (200, {"id": 7, "state": "starting"}),
            (200, {"id": 7, "state": "success", "log": ["done"]}),
        ]
    )
    monkeypatch.setattr(
        "regimpact.agents.fabric_livy_client.time.sleep", lambda _s: None
    )

    payload = client.wait_for_completion(
        "7", timeout_seconds=60, poll_interval_seconds=1
    )

    assert payload["state"] == "success"
    assert len(calls) == 2
    assert all(c["method"] == "GET" for c in calls)
    assert all(c["url"].endswith("/batches/7") for c in calls)


@pytest.mark.parametrize("bad_state", ["dead", "error", "killed"])
def test_wait_for_completion_terminal_failure_raises(
    monkeypatch: pytest.MonkeyPatch, bad_state: str
) -> None:
    client, _ = _make_client([(200, {"id": 9, "state": bad_state})])
    monkeypatch.setattr(
        "regimpact.agents.fabric_livy_client.time.sleep", lambda _s: None
    )
    with pytest.raises(LivyExecutionError) as excinfo:
        client.wait_for_completion(
            "9", timeout_seconds=10, poll_interval_seconds=1
        )
    assert bad_state in str(excinfo.value)


def test_wait_for_completion_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # Always return a non-terminal state. Deterministic ``time.monotonic`` so
    # the second call exceeds the timeout.
    client, _ = _make_client(
        [
            (200, {"id": 3, "state": "starting"}),
            (200, {"id": 3, "state": "busy"}),
            (200, {"id": 3, "state": "busy"}),
        ]
    )
    times = iter([100.0, 100.0, 200.0, 300.0])
    monkeypatch.setattr(
        "regimpact.agents.fabric_livy_client.time.monotonic",
        lambda: next(times),
    )
    monkeypatch.setattr(
        "regimpact.agents.fabric_livy_client.time.sleep", lambda _s: None
    )

    with pytest.raises(LivyTimeoutError) as excinfo:
        client.wait_for_completion(
            "3", timeout_seconds=30, poll_interval_seconds=1
        )
    assert "30s" in str(excinfo.value)


def test_wait_for_completion_http_error_raises_submit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _make_client([(503, {"error": "unavailable"})])
    monkeypatch.setattr(
        "regimpact.agents.fabric_livy_client.time.sleep", lambda _s: None
    )
    with pytest.raises(LivySubmitError):
        client.wait_for_completion(
            "1", timeout_seconds=10, poll_interval_seconds=1
        )


def test_wait_for_completion_rejects_empty_batch_id() -> None:
    client, _ = _make_client([])
    with pytest.raises(LivySubmitError):
        client.wait_for_completion("")
