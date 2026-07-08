"""Regulation Interpreter agent for converting regulatory change text."""
from ..contracts import (
    InterpretRequest,
    InterpretResponse,
)
from .foundry_interpreter import (
    FoundryInterpreterAdapter,
    FoundryInterpreterError,
    foundry_runtime_error_types,
)


class InterpreterAgent:
    """
    Regulation Interpreter agent.

    Converts regulatory change text into structured obligations through the
    Foundry-backed Agent Framework adapter. Foundry setup, auth, invocation,
    and validation errors are surfaced to the caller instead of being masked.
    """

    def __init__(
        self,
        foundry_adapter: FoundryInterpreterAdapter | None = None,
    ):
        self.foundry_adapter = foundry_adapter

    def interpret(self, request: InterpretRequest) -> InterpretResponse:
        """
        Interpret a regulatory change into structured obligations.

        Args:
            request: Validated interpret request

        Returns:
            InterpretResponse with obligations and validation status

        Raises:
            ValidationError: If request validation fails.
            FoundryInterpreterError: If Foundry setup, auth, invocation, or
                response validation fails.
        """
        request.validate()
        adapter = self.foundry_adapter or FoundryInterpreterAdapter()
        try:
            return adapter.interpret(request)
        except FoundryInterpreterError:
            raise
        except foundry_runtime_error_types() as exc:
            raise FoundryInterpreterError("Foundry interpreter invocation failed") from exc

    def interpret_changes(self, document_content: str) -> dict:
        """
        Legacy method for interpreting regulatory changes.

        Deprecated: Use interpret() with InterpretRequest instead.
        """
        request = InterpretRequest(
            regulation_id="REG-UPLOAD",
            change_id="CHG-UPLOAD",
            name="Uploaded Regulation",
            title="Uploaded regulatory change",
            source_text=document_content,
        )
        response = self.interpret(request)
        return {
            "status": response.mode,
            "changes": [obligation.__dict__ for obligation in response.obligations],
        }
