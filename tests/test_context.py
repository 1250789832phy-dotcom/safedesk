from context.index import ContextIndex, chunk_markdown, select_context
from context.memory import MemoryStore
from agent.service import AgentService
from agent.models import Plan
from agent.tools import ToolRegistry


def test_chunking_preserves_heading_and_source_lines():
    chunks = chunk_markdown(
        "# Refunds\n\nApply within seven days.\n\n# Shipping\n\nContact the seller first.",
        source="docs/policy.md",
    )

    assert len(chunks) == 2
    assert chunks[0].heading == "Refunds"
    assert chunks[0].source == "docs/policy.md"
    assert chunks[0].start_line == 1
    assert "seven days" in chunks[0].text


def test_keyword_search_reports_source_and_explicit_mode():
    index = ContextIndex.from_documents(
        {"docs/policy.md": "# Refunds\n\nApply within seven days."}
    )

    result = index.search("refunds")

    assert result.mode == "keyword_fallback"
    assert result.matches[0].source == "docs/policy.md"
    assert result.matches[0].heading == "Refunds"


def test_unmatched_query_is_reported_without_fabricated_source():
    index = ContextIndex.from_documents(
        {"docs/policy.md": "# Refunds\n\nApply within seven days."}
    )

    result = index.search("espresso machine")

    assert result.matches == []
    assert result.mode == "keyword_fallback"


def test_mock_embedding_retrieves_similar_vectors():
    index = ContextIndex.from_documents(
        {
            "docs/a.md": "The purchase may be refunded after seven days.",
            "docs/b.md": "Reboot the router to restore Wi-Fi.",
        },
        embedding_provider=lambda text: [1.0, 0.0] if "refund" in text else [0.0, 1.0],
    )

    result = index.search("refund purchase", mode="embedding")

    assert result.mode == "mock_embedding"
    assert result.matches[0].source == "docs/a.md"


def test_context_budget_is_deterministic_and_reports_truncation():
    index = ContextIndex.from_documents(
        {
            "docs/a.md": "# A\n\nalpha alpha alpha",
            "docs/b.md": "# B\n\nbeta beta beta",
        }
    )
    result = index.search("alpha beta")

    selected = select_context(result.matches, token_budget=4)

    assert sum(item.estimated_tokens for item in selected.included) <= 4
    assert selected.truncated is True
    assert selected.excluded_sources


def test_memory_is_separate_clearable_and_updates_old_preferences():
    memory = MemoryStore()

    memory.set_preference("output", "voice")
    memory.set_preference("output", "text")

    assert memory.get_preference("output") == "text"
    assert memory.search_knowledge("output") == []
    memory.clear()
    assert memory.get_preference("output") is None


def test_retrieved_context_and_sources_are_passed_to_the_planner():
    index = ContextIndex.from_documents(
        {"docs/policy.md": "# Refunds\n\nRefunds require a receipt."}
    )

    class ContextPlanner:
        seen_request = ""

        def plan(self, request):
            self.seen_request = request
            return Plan(
                summary="Answer from the refund policy",
                acceptance_criteria=["Use retrieved policy context"],
                tool_calls=[
                    {
                        "name": "read_file",
                        "arguments": {"path": "example.txt"},
                    }
                ],
            )

        def finalize(self, request, plan, observations):
            return "checked"

    planner = ContextPlanner()
    service = AgentService(
        planner=planner,
        tools=ToolRegistry(),
        context_index=index,
    )

    record = service.submit("How do refunds work?")

    assert "Refunds require a receipt" in planner.seen_request
    assert record.retrieval_mode == "keyword_fallback"
    assert record.context_sources == ["docs/policy.md"]
