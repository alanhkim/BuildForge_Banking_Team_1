class InterpreterAgent:
    def __init__(self, use_offline_fallback: bool = True):
        self.use_offline_fallback = use_offline_fallback

    def interpret_changes(self, document_content: str) -> dict:
        """
        Interprets regulatory changes.
        """
        if self.use_offline_fallback:
            return self._offline_fallback_logic(document_content)
        # TODO: Implement Azure OpenAI integration
        pass

    def _offline_fallback_logic(self, document_content: str) -> dict:
        """
        Offline fallback logic for interpreting regulatory changes.
        """
        return {"status": "offline_interpretation", "changes": []}
