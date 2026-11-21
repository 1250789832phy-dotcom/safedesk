import math

import pytest

from context import index as retrieval
from context.index import ContextIndex


def test_hash_embedding_deterministic():
    vector = retrieval._hash_embedding("Refund policy 退款")

    assert vector == retrieval._hash_embedding("Refund policy 退款")
    assert len(vector) == 256
    assert math.isclose(sum(value * value for value in vector), 1.0)
    assert vector == retrieval._hash_embedding("REFUND POLICY 退款")
    assert vector != retrieval._hash_embedding("router reboot")
    assert retrieval._hash_embedding("ab") != retrieval._hash_embedding("ba")


def test_hash_embedding_empty_and_custom_dimension():
    assert retrieval._hash_embedding("") == [0.0] * 256
    assert len(retrieval._hash_embedding("sample", dim=16)) == 16
    with pytest.raises(ValueError, match="positive"):
        retrieval._hash_embedding("sample", dim=0)


def test_all_factories_enable_local_embedding(tmp_path):
    text = "refund policy"
    (tmp_path / "policy.md").write_text(text, encoding="utf-8")
    chunk = retrieval.Chunk("policy.md", 1, 1, None, text)
    indexes = [
        ContextIndex([chunk]),
        ContextIndex.from_documents({"policy.md": text}),
        ContextIndex.from_project(tmp_path),
    ]

    for index in indexes:
        assert len(index.chunks[0].vector or []) == 256
        result = index.search(text, mode="embedding")
        assert result.mode == "local_hash_embedding"
        assert result.matches[0].source == "policy.md"


def test_hybrid_search_finds_chunk():
    index = ContextIndex.from_documents(
        {"keyword.md": "refund policy", "vector.md": "refnd polcy"}
    )

    keyword = index.search("refund policy")
    result = index.search("refund policy", mode="hybrid")

    assert [chunk.source for chunk in keyword.matches] == ["keyword.md"]
    assert retrieval._cosine(
        index.embedding_provider("refund policy"), index.chunks[1].vector
    ) > 0
    assert result.mode == "hybrid"
    assert {chunk.source for chunk in result.matches} == {"keyword.md", "vector.md"}


def test_hybrid_ranking_normalizes_keyword_score_and_limits_results():
    documents = {
        "keyword.md": "alpha beta opposite",
        "mixed.md": "alpha near",
    }
    documents.update({f"vector-{n}.md": f"near-{n}" for n in range(7)})
    index = ContextIndex.from_documents(
        documents,
        embedding_provider=lambda text: (
            [-1.0, 0.0] if "opposite" in text else [1.0, 0.0]
        ),
    )

    result = index.search("alpha beta", mode="hybrid")

    assert result.mode == "hybrid"
    # Mixed evidence scores .75; full keywords/opposite vector and vector-only tie .5.
    assert [chunk.source for chunk in result.matches[:2]] == [
        "mixed.md", "keyword.md"
    ]
    assert len(result.matches) == 5


@pytest.mark.parametrize("mode", ["embedding", "hybrid"])
def test_explicit_none_disables_vector_modes(mode):
    index = ContextIndex.from_documents({"a.md": "alpha"}, embedding_provider=None)
    assert index.chunks[0].vector is None
    with pytest.raises(ValueError, match="embedding provider"):
        index.search("alpha", mode=mode)


def test_hybrid_does_not_invent_matches_for_empty_query():
    index = ContextIndex.from_documents({"a.md": "alpha"})
    assert index.search("", mode="hybrid").matches == []
