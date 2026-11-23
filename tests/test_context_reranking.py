import pytest

from agent.models import Plan, ToolCall
from agent.service import AgentService
from context.index import ContextIndex
from context.rerank import rerank_chunks


class ContextPlanner:
    seen_request = ""

    def plan(self, request):
        self.seen_request = request
        return Plan(
            summary="Read the example",
            acceptance_criteria=["Read successfully"],
            tool_calls=[ToolCall(name="read_file", arguments={"path": "example.txt"})],
        )

    def finalize(self, request, plan, observations):
        return observations[-1].message


def _refund_index():
    documents = {f"other-{n}.md": "refund general policy" for n in range(5)}
    documents["policy.md"] = "Refund   POLICY"
    return ContextIndex.from_documents(documents, embedding_provider=lambda _: [1.0])


def test_default_service_promotes_sixth_candidate_before_context_budget():
    index = _refund_index()
    initial = index.search("refund policy", "embedding")
    assert [chunk.source for chunk in initial.matches] == [f"other-{n}.md" for n in range(5)]
    planner = ContextPlanner()

    record = AgentService(
        planner=planner, context_index=index, context_token_budget=4
    ).submit("refund policy")

    assert record.status == "succeeded"
    assert record.retrieval_mode == "local_hash_embedding"
    assert record.context_sources == ["policy.md"]
    assert record.context_truncated
    assert "Refund   POLICY" in planner.seen_request


@pytest.mark.parametrize("mode", ["keyword", "embedding", "hybrid"])
def test_search_limit_expands_recall_without_changing_rank(mode):
    index = _refund_index()

    default = index.search("refund policy", mode)
    expanded = index.search("refund policy", mode, limit=20)

    assert expanded.mode == default.mode
    assert expanded.matches[:5] == default.matches
    assert expanded.matches[5].source == "policy.md"


def test_search_rejects_nonpositive_limit():
    with pytest.raises(ValueError, match="limit"):
        _refund_index().search("refund policy", limit=0)


def test_reranking_can_be_disabled():
    record = AgentService(
        planner=ContextPlanner(), context_index=_refund_index(), rerank_context=False
    ).submit("refund policy")

    assert record.context_sources == [f"other-{n}.md" for n in range(5)]


def test_reranking_does_not_add_documents_outside_the_candidate_pool():
    documents = {f"other-{n}.md": "refund general policy" for n in range(20)}
    documents["policy.md"] = "refund policy"
    index = ContextIndex.from_documents(documents, embedding_provider=lambda _: [1.0])

    record = AgentService(planner=ContextPlanner(), context_index=index).submit(
        "refund policy"
    )

    assert record.context_sources == [f"other-{n}.md" for n in range(5)]


def test_keyword_miss_does_not_invent_context():
    record = AgentService(
        planner=ContextPlanner(), context_index=_refund_index(), retrieval_mode="keyword"
    ).submit("unrelated")

    assert record.context_sources == []


def test_chinese_literal_phrase_is_promoted_after_hash_recall():
    query = "\u9000\u8d27"
    index = ContextIndex.from_documents(
        {
            "spaced.md": "\u9000 \u8d27",
            "exchange.md": "\u9000\u6362\u8d27",
            "policy.md": (
                "\u9000\u8d27\u524d\u8bf7\u4fdd\u7559\u8ba2\u5355\u8bb0\u5f55\u3002"
                * 8
            ),
        }
    )
    recalled = index.search(query, mode="embedding", limit=20).matches

    assert recalled[0].source != "policy.md"
    assert rerank_chunks(query, recalled)[0].source == "policy.md"


def test_reranker_prefers_more_query_words_when_literal_phrase_is_absent():
    index = ContextIndex.from_documents(
        {"one.md": "refund", "both.md": "policy for refund", "tie.md": "refund window"}
    )

    ranked = rerank_chunks("refund policy", index.chunks)

    assert [chunk.source for chunk in ranked] == ["both.md", "one.md", "tie.md"]
