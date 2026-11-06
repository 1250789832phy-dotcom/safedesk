class MemoryStore:
    def __init__(self):
        self.preferences: dict[str, str] = {}
        self.summaries: dict[str, str] = {}

    def set_preference(self, key: str, value: str) -> None:
        self.preferences[key] = value

    def get_preference(self, key: str) -> str | None:
        return self.preferences.get(key)

    def save_summary(self, session_id: str, summary: str) -> None:
        self.summaries[session_id] = summary

    def get_summary(self, session_id: str) -> str | None:
        return self.summaries.get(session_id)

    def search_knowledge(self, query: str) -> list[str]:
        return []

    def clear(self) -> None:
        self.preferences.clear()
        self.summaries.clear()
