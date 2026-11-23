"""Shared retrieval pipeline for live requests and offline evaluations."""

from context.index import ContextIndex, DEFAULT_SEARCH_LIMIT, select_context
from context.rerank import rerank_chunks

RERANK_CANDIDATE_LIMIT = 20


def retrieve(
    index: ContextIndex, query: str, *, mode: str, rerank: bool, token_budget: int,
):
    limit = RERANK_CANDIDATE_LIMIT if rerank else DEFAULT_SEARCH_LIMIT
    search = index.search(query, mode=mode, limit=limit)
    ranked = rerank_chunks(query, search.matches)[:DEFAULT_SEARCH_LIMIT] if rerank else search.matches
    return search.mode, ranked, select_context(ranked, token_budget=token_budget)
