"""
Regulation Interpreter agent for converting regulatory change text
into structured obligations.

Provides deterministic offline fallback using catalog fixtures.
"""
from ..contracts import (
    InterpretRequest,
    InterpretResponse,
)
from ..catalog import CatalogFixture
from ..settings import Settings, settings
from .foundry_interpreter import (
    FoundryInterpreterAdapter,
    FoundryInterpreterError,
    foundry_runtime_error_types,
)


class InterpreterAgent:
    """
    Regulation Interpreter agent.

    Converts regulatory change text into structured obligations using
    deterministic catalog fallback for known regulations like DORA.
    """

    def __init__(
        self,
        use_offline_fallback: bool = True,
        foundry_adapter: FoundryInterpreterAdapter | None = None,
        app_settings: Settings = settings,
    ):
        self.use_offline_fallback = use_offline_fallback
        self.catalog = CatalogFixture()
        self.foundry_adapter = foundry_adapter
        self.settings = app_settings

    def interpret(self, request: InterpretRequest) -> InterpretResponse:
        """
        Interpret a regulatory change into structured obligations.

        Args:
            request: Validated interpret request

        Returns:
            InterpretResponse with obligations and validation status

        Raises:
            ValidationError: If request validation fails
        """
        # Validate request
        request.validate()

        # Use offline fallback if enabled or required
        if self.use_offline_fallback or request.offline_mode:
            return self._deterministic_interpretation(request)

        if self.settings.foundry_enabled:
            try:
                adapter = self.foundry_adapter or FoundryInterpreterAdapter()
                return adapter.interpret(request)
            except (FoundryInterpreterError, *foundry_runtime_error_types()) as exc:
                response = self._deterministic_interpretation(request)
                response.notes.append(f"Foundry unavailable; deterministic fallback used: {exc}")
                return response

        return self._deterministic_interpretation(request)

    def _deterministic_interpretation(self, request: InterpretRequest) -> InterpretResponse:
        """
        Deterministic interpretation using catalog fixtures.

        Args:
            request: Interpret request

        Returns:
            InterpretResponse with catalog obligations or empty result
        """
        # Check catalog for known regulation
        obligations = self.catalog.get_obligations(
            request.regulation_id,
            request.change_id
        )

        notes = []
        if obligations is not None:
            # Validate all obligations before returning
            for obl in obligations:
                obl.validate()
            notes.append(f"Retrieved {len(obligations)} obligation(s) from catalog")
        else:
            # Unknown regulation: return empty obligations with note
            obligations = []
            notes.append(
                f"No catalog entry found for {request.regulation_id}/{request.change_id}. "
                f"Known regulations: {', '.join(self.catalog.list_regulations())}"
            )

        response = InterpretResponse(
            regulation_id=request.regulation_id,
            change_id=request.change_id,
            obligations=obligations,
            mode="deterministic-fallback",
            notes=notes,
        )

        # Validate response before returning
        response.validate()
        return response

    def interpret_changes(self, document_content: str) -> dict:
        """
        Legacy method for interpreting regulatory changes.

        Deprecated: Use interpret() with InterpretRequest instead.
        """
        if self.use_offline_fallback:
            return self._offline_fallback_logic(document_content)
        return {"status": "offline_interpretation", "changes": []}

    def _offline_fallback_logic(self, document_content: str) -> dict:
        """
        Legacy offline fallback logic.

        Deprecated: Use _deterministic_interpretation() instead.
        """
        return {"status": "offline_interpretation", "changes": []}
