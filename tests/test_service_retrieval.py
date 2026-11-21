import pytest

from agent.models import Plan, ToolCall
from agent.service import AgentService
from context.index import ContextIndex


class RetrievalPlanner:
    seen_request = ""

    def plan(self, request):
        self.seen_request = request
        return Plan(
            summary="Read the example",
            acceptance_criteria=["Read successfully"],
            tool_calls=[
                ToolCall(name="read_file", arguments={"path": "example.txt"})
            ],
        )

    def finalize(self, request, plan, observations):
        return observations[-1].message


def test_service_defaults_to_local_embedding_and_passes_vector_only_hit():
    index = ContextIndex.from_documents({"policy.md": "refnd polcy"})
    assert index.search("refund policy", mode="keyword").matches == []
    planner = RetrievalPlanner()

    record = AgentService(
        planner=planner, context_index=index
    ).submit("refund policy")

    assert record.status == "succeeded"
    assert record.retrieval_mode == "local_hash_embedding"
    assert record.context_sources == ["policy.md"]
    assert "refnd polcy" in planner.seen_request


@pytest.mark.parametrize(
    ("mode", "result_mode"),
    [
        ("keyword", "keyword_fallback"),
        ("embedding", "local_hash_embedding"),
        ("hybrid", "hybrid"),
    ],
)
def test_service_can_explicitly_choose_retrieval_mode(mode, result_mode):
    service = AgentService(
        planner=RetrievalPlanner(),
        context_index=ContextIndex.from_documents({"policy.md": "refund policy"}),
        retrieval_mode=mode,
    )

    record = service.submit("refund policy")

    assert record.status == "succeeded"
    assert record.retrieval_mode == result_mode
    assert record.context_sources == ["policy.md"]


def test_service_rejects_unknown_retrieval_mode():
    with pytest.raises(ValueError, match="retrieval mode"):
        AgentService(retrieval_mode="unknown")


def test_service_requires_provider_for_vector_mode_but_allows_keyword():
    index = ContextIndex.from_documents(
        {"policy.md": "refund policy"}, embedding_provider=None
    )
    with pytest.raises(ValueError, match="embedding provider"):
        AgentService(context_index=index)

    record = AgentService(
        planner=RetrievalPlanner(), context_index=index, retrieval_mode="keyword"
    ).submit("refund policy")
    assert record.status == "succeeded"
    assert record.retrieval_mode == "keyword_fallback"
