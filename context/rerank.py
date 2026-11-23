import re

from context.index import Chunk


def rerank_chunks(query: str, candidates: list[Chunk]) -> list[Chunk]:
    """Prefer literal phrases and word coverage, keeping recall order on ties."""
    phrase = " ".join(query.casefold().split())
    terms = set(re.findall(r"\w+", phrase))
    if not terms:
        return candidates.copy()

    def score(chunk: Chunk) -> tuple[bool, int]:
        heading = " ".join((chunk.heading or "").casefold().split())
        text = " ".join(chunk.text.casefold().split())
        words = set(re.findall(r"\w+", f"{heading} {text}"))
        return phrase in heading or phrase in text, len(terms & words)

    return sorted(candidates, key=score, reverse=True)
