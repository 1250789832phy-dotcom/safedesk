import math
import re
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


DEFAULT_SEARCH_LIMIT = 5


@dataclass(frozen=True)
class Chunk:
    source: str
    start_line: int
    end_line: int
    heading: str | None
    text: str
    vector: list[float] | None = None


@dataclass(frozen=True)
class SearchResult:
    mode: str
    matches: list[Chunk]


@dataclass(frozen=True)
class ContextSelection:
    included: list[Chunk]
    excluded_sources: list[str]
    estimated_tokens: int
    truncated: bool


def _hash_embedding(text: str, dim: int = 256) -> list[float]:
    if dim <= 0:
        raise ValueError("Embedding dimension must be positive")
    vector = [0.0] * dim
    folded = text.casefold()
    for width in (1, 2):
        for start in range(len(folded) - width + 1):
            hashed = zlib.crc32(folded[start : start + width].encode("utf-8"))
            vector[hashed % dim] += -1.0 if hashed & 0x80000000 else 1.0
    magnitude = math.sqrt(sum(value * value for value in vector))
    return [value / magnitude for value in vector] if magnitude else vector


def chunk_markdown(text: str, source: str) -> list[Chunk]:
    lines = text.splitlines()
    chunks: list[Chunk] = []
    heading: str | None = None
    section_start = 1
    section_lines: list[tuple[int, str]] = []

    def flush():
        nonlocal section_lines
        content = "\n".join(line for _, line in section_lines).strip()
        if content:
            chunks.append(
                Chunk(
                    source=source,
                    start_line=section_start,
                    end_line=section_lines[-1][0],
                    heading=heading,
                    text=content,
                )
            )
        section_lines = []

    for line_number, line in enumerate(lines, start=1):
        if line.startswith("#"):
            flush()
            heading = line.lstrip("#").strip() or None
            section_start = line_number
            section_lines.append((line_number, line))
        elif not line.strip() and section_lines:
            section_lines.append((line_number, line))
        elif line.strip():
            if not section_lines:
                section_start = line_number
            section_lines.append((line_number, line))
    flush()

    return chunks or (
        [
            Chunk(
                source=source,
                start_line=1,
                end_line=max(1, len(lines)),
                heading=None,
                text=text.strip(),
            )
        ]
        if text.strip()
        else []
    )


def chunk_python(text: str, source: str) -> list[Chunk]:
    lines = text.splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if re.match(r"^(?:async\s+)?(?:def|class)\s+\w+", line)
    ]
    if not starts:
        return chunk_markdown(text, source)
    chunks = []
    prefix = "\n".join(lines[: starts[0]]).strip()
    if prefix:
        chunks.append(
            Chunk(source, 1, starts[0], "module", prefix)
        )
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end]).strip()
        if block:
            match = re.match(r"^(?:async\s+)?(?:def|class)\s+(\w+)", lines[start])
            chunks.append(
                Chunk(source, start + 1, end, match.group(1) if match else None, block)
            )
    return chunks


class ContextIndex:
    def __init__(
        self,
        chunks: list[Chunk],
        embedding_provider: Callable[[str], list[float]] | None = _hash_embedding,
    ):
        self.embedding_provider = embedding_provider
        self.chunks = [
            Chunk(
                **{
                    **chunk.__dict__,
                    "vector": embedding_provider(chunk.text)
                    if embedding_provider
                    else None,
                }
            )
            for chunk in chunks
        ]

    @classmethod
    def from_documents(
        cls,
        documents: dict[str, str],
        embedding_provider: Callable[[str], list[float]] | None = _hash_embedding,
    ):
        chunks = []
        for source, text in documents.items():
            if Path(source).suffix.lower() == ".py":
                chunks.extend(chunk_python(text, source))
            else:
                chunks.extend(chunk_markdown(text, source))
        return cls(chunks, embedding_provider)

    @classmethod
    def from_project(
        cls,
        root: Path,
        embedding_provider: Callable[[str], list[float]] | None = _hash_embedding,
    ):
        documents = {}
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".md", ".py"}:
                continue
            if any(
                part
                in {".venv", "__pycache__", ".pytest_cache", ".git", "data", "logs"}
                for part in path.parts
            ):
                continue
            try:
                documents[path.relative_to(root).as_posix()] = path.read_text(
                    encoding="utf-8-sig"
                )
            except (OSError, UnicodeError):
                continue
        return cls.from_documents(documents, embedding_provider)

    def search(
        self, query: str, mode: str = "keyword", *, limit: int = DEFAULT_SEARCH_LIMIT
    ) -> SearchResult:
        if mode not in {"keyword", "embedding", "hybrid"}:
            raise ValueError(f"Unsupported retrieval mode: {mode}")
        if limit < 1:
            raise ValueError("Search limit must be positive")
        if mode in {"embedding", "hybrid"}:
            if self.embedding_provider is None:
                raise ValueError("Embedding mode requires an embedding provider")
            query_vector = self.embedding_provider(query)
            if mode == "embedding":
                ranked = sorted(
                    (
                        (_cosine(query_vector, chunk.vector or []), chunk)
                        for chunk in self.chunks
                    ),
                    key=lambda item: item[0],
                    reverse=True,
                )
                matches = [chunk for score, chunk in ranked if score > 0][:limit]
                return SearchResult("local_hash_embedding", matches)
        terms = {word.casefold() for word in re.findall(r"\w+", query) if len(word) > 1}
        ranked = []
        for chunk in self.chunks:
            text = f"{chunk.heading or ''}\n{chunk.text}".casefold()
            score = sum(term in text for term in terms)
            ranked.append((score, chunk))
        if mode == "hybrid":
            max_keyword = max((score for score, _ in ranked), default=0) or 1
            combined = []
            for keyword_score, chunk in ranked:
                cosine = _cosine(query_vector, chunk.vector or [])
                if keyword_score or cosine > 0:
                    score = (
                        0.5 * keyword_score / max_keyword
                        + 0.5 * (cosine + 1) / 2
                    )
                    combined.append((score, chunk))
            combined.sort(key=lambda item: item[0], reverse=True)
            return SearchResult("hybrid", [chunk for _, chunk in combined[:limit]])
        ranked.sort(key=lambda item: item[0], reverse=True)
        return SearchResult(
            "keyword_fallback", [chunk for score, chunk in ranked if score][:limit]
        )


def select_context(matches: list[Chunk], token_budget: int) -> ContextSelection:
    if token_budget < 0:
        raise ValueError("token_budget must be non-negative")
    included = []
    excluded = []
    total = 0
    for chunk in matches:
        cost = max(1, math.ceil(len(chunk.text) / 4))
        if total + cost <= token_budget:
            included.append(chunk)
            total += cost
        else:
            excluded.append(chunk.source)
    return ContextSelection(included, excluded, total, bool(excluded))


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    magnitude = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return sum(a * b for a, b in zip(left, right)) / magnitude if magnitude else 0.0
