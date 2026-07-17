"""HTTP client for Microsoft Fabric's Livy Batch API.

Fabric exposes a Livy-compatible REST surface per lakehouse that lets you
submit a Spark batch job (a block of PySpark source) and poll for its
terminal state. This module wraps that transport in a small, deterministic
client the :class:`FabricMaterializerAgent` can drive.

Design notes
------------
* Only the Python standard library is imported at module scope. ``azure.identity``
  is lazy-imported inside :meth:`FabricLivyClient._get_token` so the ``regimpact``
  package still imports cleanly without the optional ``fabric`` extra installed
  (same pattern as :mod:`regimpact.lakehouse`).
* HTTP transport goes through :func:`urllib.request.urlopen` for zero extra
  dependencies. Tests replace :meth:`FabricLivyClient._do_http` — no network is
  ever touched.
* Failure modes are explicit exception classes (base :class:`LivyError`) so
  callers can map them cleanly onto CLI user-facing messages. There is
  deliberately no offline fallback — constitutional constraint.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

FABRIC_API_BASE = "https://api.fabric.microsoft.com"
LIVY_API_VERSION = "2023-12-01"
FABRIC_TOKEN_SCOPE = "https://api.fabric.microsoft.com/.default"

# Livy batch states.
_TERMINAL_STATES: frozenset[str] = frozenset(
    {"success", "dead", "error", "killed"}
)
_SUCCESS_STATE = "success"


class LivyError(Exception):
    """Base class for all Fabric Livy client failures."""


class LivyNotConfiguredError(LivyError):
    """Raised when workspace_id or lakehouse_id is missing / empty."""


class LivySubmitError(LivyError):
    """Raised when the POST that submits a batch fails at the HTTP layer."""


class LivyExecutionError(LivyError):
    """Raised when a batch reaches a terminal non-success state."""


class LivyTimeoutError(LivyError):
    """Raised when a batch does not reach a terminal state within the timeout."""


class FabricLivyClient:
    """Client for the per-lakehouse Fabric Livy Batch API.

    Parameters
    ----------
    workspace_id:
        Fabric workspace GUID.
    lakehouse_id:
        Fabric lakehouse GUID within the workspace.
    credential:
        Optional Azure credential exposing ``get_token(scope)``. Defaults to
        :class:`azure.identity.DefaultAzureCredential` (lazy-imported).
    """

    def __init__(
        self,
        workspace_id: str,
        lakehouse_id: str,
        credential: Any = None,
    ) -> None:
        if not workspace_id:
            raise LivyNotConfiguredError(
                "FABRIC_WORKSPACE_ID is not set; cannot call Fabric Livy API."
            )
        if not lakehouse_id:
            raise LivyNotConfiguredError(
                "FABRIC_LAKEHOUSE_ID is not set; cannot call Fabric Livy API."
            )
        self.workspace_id = workspace_id
        self.lakehouse_id = lakehouse_id
        self._credential = credential

    # ------------------------------------------------------------------ URLs
    @property
    def _batches_url(self) -> str:
        return (
            f"{FABRIC_API_BASE}/v1/workspaces/{self.workspace_id}"
            f"/lakehouses/{self.lakehouse_id}"
            f"/livyApi/versions/{LIVY_API_VERSION}/batches"
        )

    def _batch_url(self, batch_id: str) -> str:
        return f"{self._batches_url}/{batch_id}"

    # ------------------------------------------------------------------ auth
    def _get_token(self) -> str:
        """Return a bearer token for the Fabric API.

        ``azure.identity`` is lazy-imported so this module works without the
        optional ``fabric`` extra when tests inject a stub credential.
        """
        credential = self._credential
        if credential is None:
            from azure.identity import DefaultAzureCredential

            credential = DefaultAzureCredential()
            self._credential = credential
        token = credential.get_token(FABRIC_TOKEN_SCOPE)
        return token.token

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ---------------------------------------------------------- HTTP transport
    def _do_http(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any] | None = None,
        timeout_seconds: float = 30.0,
    ) -> tuple[int, dict[str, Any]]:
        """Execute a single HTTP request. Returns ``(status_code, json_body)``.

        This is the ONLY seam that tests replace. It always uses stdlib
        ``urllib.request`` so the package has no runtime HTTP dependency
        beyond the standard library.
        """
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = Request(url, method=method.upper(), data=data, headers=headers)
        try:
            with urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310
                status = int(resp.status)
                raw = resp.read()
        except HTTPError as exc:
            raw = b""
            try:
                raw = exc.read()
            except Exception:  # noqa: BLE001
                pass
            payload = _safe_json(raw)
            return int(exc.code), payload
        except URLError as exc:
            raise LivySubmitError(
                f"{method.upper()} {url} network error: {exc.reason}"
            ) from exc
        payload = _safe_json(raw)
        return status, payload

    # ---------------------------------------------------------- public methods
    def submit_batch(
        self,
        pyspark_code: str,
        name: str = "regimpact-materialize",
    ) -> str:
        """Submit a PySpark batch job.

        Parameters
        ----------
        pyspark_code:
            The full PySpark source to execute inside the Fabric Spark session.
        name:
            Batch display name (surfaced in the Fabric monitor UI).

        Returns
        -------
        str
            The Livy batch identifier.

        Raises
        ------
        LivySubmitError
            On any non-2xx response, transport error, or missing ``id``.
        """
        if not pyspark_code or not pyspark_code.strip():
            raise LivySubmitError("pyspark_code must be a non-empty string")

        body = {"name": name, "language": "python", "code": pyspark_code}
        status, payload = self._do_http(
            "POST",
            self._batches_url,
            headers=self._auth_headers(),
            body=body,
        )
        if not (200 <= status < 300):
            raise LivySubmitError(
                f"Fabric Livy submit failed: HTTP {status} — {_short(payload)}"
            )
        batch_id = payload.get("id")
        if batch_id is None:
            raise LivySubmitError(
                f"Fabric Livy submit returned no batch id (payload={_short(payload)})"
            )
        logger.info(
            "Livy batch submitted: id=%s workspace=%s lakehouse=%s",
            batch_id,
            self.workspace_id,
            self.lakehouse_id,
        )
        return str(batch_id)

    def wait_for_completion(
        self,
        batch_id: str,
        timeout_seconds: int = 600,
        poll_interval_seconds: int = 5,
    ) -> dict[str, Any]:
        """Poll a batch until it reaches a terminal state or the timeout elapses.

        Parameters
        ----------
        batch_id:
            The identifier returned from :meth:`submit_batch`.
        timeout_seconds:
            Maximum time to wait before raising :class:`LivyTimeoutError`.
        poll_interval_seconds:
            Seconds between GET polls.

        Returns
        -------
        dict
            The final Livy batch status payload (state == ``"success"``).

        Raises
        ------
        LivyExecutionError
            The batch reached a terminal non-success state
            (``dead`` / ``error`` / ``killed``).
        LivyTimeoutError
            The batch did not reach a terminal state before ``timeout_seconds``.
        LivySubmitError
            The GET poll itself failed at the HTTP layer.
        """
        if not batch_id:
            raise LivySubmitError("batch_id must be non-empty")

        start = time.monotonic()
        last_state = "unknown"
        while True:
            status, payload = self._do_http(
                "GET",
                self._batch_url(batch_id),
                headers=self._auth_headers(),
            )
            if not (200 <= status < 300):
                raise LivySubmitError(
                    f"Fabric Livy poll failed for batch {batch_id}: "
                    f"HTTP {status} — {_short(payload)}"
                )
            state = str(payload.get("state") or "").lower()
            last_state = state or last_state
            if state in _TERMINAL_STATES:
                if state == _SUCCESS_STATE:
                    logger.info(
                        "Livy batch %s completed successfully.", batch_id
                    )
                    return payload
                raise LivyExecutionError(
                    f"Fabric Livy batch {batch_id} ended in state '{state}': "
                    f"{_short(payload)}"
                )
            elapsed = time.monotonic() - start
            if elapsed >= timeout_seconds:
                raise LivyTimeoutError(
                    f"Fabric Livy batch {batch_id} did not reach a terminal "
                    f"state within {timeout_seconds}s "
                    f"(last state='{last_state}')."
                )
            time.sleep(poll_interval_seconds)


def _safe_json(raw: bytes) -> dict[str, Any]:
    """Parse a JSON body defensively — never raise from a poll loop."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"_raw": raw[:500].decode("utf-8", errors="replace")}
    if isinstance(parsed, dict):
        return parsed
    return {"_value": parsed}


def _short(payload: dict[str, Any], limit: int = 300) -> str:
    """Compact repr of a payload for exception messages."""
    try:
        text = json.dumps(payload, sort_keys=True)
    except TypeError:
        text = repr(payload)
    if len(text) > limit:
        return text[:limit] + "…"
    return text
