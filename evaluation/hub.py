"""Versioned data catalog and reproducible offline retrieval evaluation."""

import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from context.index import DEFAULT_SEARCH_LIMIT, ContextIndex, chunk_markdown, chunk_python
from context.retrieval import retrieve

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "evaluation-hub.sqlite3"
Name = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]
Source = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Mode = Literal["keyword", "embedding", "hybrid"]
MAX_CHUNKS = 1000
MAX_VERSION = 2**63 - 1


class RetrievalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Name
    query: str = Field(min_length=1, max_length=1000)
    expected_source: Source | None


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    documents: dict[Source, str] = Field(min_length=1, max_length=100)
    cases: list[RetrievalCase] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_snapshot(self):
        if sum(map(len, self.documents.values())) > 250_000:
            raise ValueError("Documents exceed the 250,000 character dataset limit")
        chunks = 0
        for name, text in self.documents.items():
            path = PurePosixPath(name)
            if (
                path.is_absolute() or path.as_posix() != name or ".." in path.parts
                or "\\" in name or ":" in name or any(ord(c) < 32 for c in name)
                or path.suffix.lower() not in {".md", ".py"}
                or not text.strip()
            ):
                raise ValueError("Documents need nonempty text and relative .md or .py names")
            splitter = chunk_python if path.suffix.lower() == ".py" else chunk_markdown
            chunks += len(splitter(text, name))
            if chunks > MAX_CHUNKS:
                raise ValueError(f"Dataset exceeds the {MAX_CHUNKS} chunk limit")
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Case IDs must be unique")
        for case in self.cases:
            if not case.query.strip() or (
                case.expected_source is not None and case.expected_source not in self.documents
            ):
                raise ValueError("Cases need a query and an existing expected source or null")
        return self


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: Name
    version: int = Field(ge=1, le=MAX_VERSION)
    mode: Mode = "keyword"
    rerank: bool = True
    token_budget: int = Field(default=1200, ge=0, le=100_000)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _ratio(total, count):
    return round(total / count, 6) if count else None


def _location(chunk):
    return {"source": chunk.source, "start_line": chunk.start_line, "end_line": chunk.end_line}


def _score(dataset: dict, config: RunConfig):
    if config.mode == "keyword":
        index = ContextIndex.from_documents(dataset["documents"], embedding_provider=None)
    else:
        index = ContextIndex.from_documents(dataset["documents"])
    results = []
    for case in dataset["cases"]:
        expected = case["expected_source"]
        _, ranked, selection = retrieve(
            index, case["query"], mode=config.mode, rerank=config.rerank,
            token_budget=config.token_budget,
        )
        rank = next((i for i, chunk in enumerate(ranked, 1) if chunk.source == expected), None)
        if expected is None:
            failure = "unexpected_retrieval" if ranked else None
        elif rank is None:
            failure = "not_ranked"
        elif not any(chunk.source == expected for chunk in selection.included):
            failure = "budget_excluded"
        else:
            failure = None
        results.append({
            **case, "rank": rank, "passed": failure is None, "failure": failure,
            "ranked": [_location(chunk) for chunk in ranked],
            "context": [_location(chunk) for chunk in selection.included],
            "estimated_tokens": selection.estimated_tokens,
            "truncated": selection.truncated,
        })

    hits = [case for case in results if case["expected_source"] is not None]
    misses = [case for case in results if case["expected_source"] is None]
    return {
        "cases": len(results),
        "pass_rate": _ratio(sum(case["passed"] for case in results), len(results)),
        "hit_at_1": _ratio(sum(case["rank"] == 1 for case in hits), len(hits)),
        "hit_at_5": _ratio(sum(
            case["rank"] is not None and case["rank"] <= DEFAULT_SEARCH_LIMIT for case in hits
        ), len(hits)),
        "mrr_at_5": _ratio(sum(1 / case["rank"] for case in hits if case["rank"]), len(hits)),
        "context_hit_rate": _ratio(sum(case["passed"] for case in hits), len(hits)),
        "no_hit_accuracy": _ratio(sum(case["passed"] for case in misses), len(misses)),
    }, results


class EvalHub:
    def __init__(self, db: Path | str = DEFAULT_DB):
        self.db = Path(db)

    @contextmanager
    def _connect(self):
        self.db.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db, timeout=10)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS datasets (
                    name TEXT NOT NULL, version INTEGER NOT NULL, digest TEXT NOT NULL,
                    created_at TEXT NOT NULL, snapshot TEXT NOT NULL,
                    PRIMARY KEY (name, version), UNIQUE (name, digest)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL,
                    created_at TEXT NOT NULL, report TEXT NOT NULL,
                    FOREIGN KEY (name, version) REFERENCES datasets (name, version)
                );
            """)
            yield conn

    @staticmethod
    def _metadata(row):
        snapshot = json.loads(row["snapshot"])
        return {
            "name": row["name"], "version": row["version"], "digest": row["digest"],
            "created_at": row["created_at"], "document_count": len(snapshot["documents"]),
            "case_count": len(snapshot["cases"]),
        }

    def register(self, value: Dataset | dict) -> dict:
        dataset = Dataset.model_validate(value)
        snapshot = _json(dataset.model_dump(exclude={"name"}))
        digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM datasets WHERE name = ? AND digest = ?", (dataset.name, digest)
            ).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO datasets (name, version, digest, created_at, snapshot)
                       SELECT ?, COALESCE(MAX(version), 0) + 1, ?, ?, ?
                       FROM datasets WHERE name = ?""",
                    (dataset.name, digest, _now(), snapshot, dataset.name),
                )
                row = conn.execute(
                    "SELECT * FROM datasets WHERE name = ? AND digest = ?", (dataset.name, digest)
                ).fetchone()
            return self._metadata(row)

    def list_datasets(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM datasets ORDER BY name, version DESC LIMIT ?", (limit,)
            )
            return [self._metadata(row) for row in rows]

    def get_dataset(self, name: str, version: int) -> dict:
        if not 1 <= version <= MAX_VERSION:
            raise KeyError("Dataset version not found")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM datasets WHERE name = ? AND version = ?", (name, version)
            ).fetchone()
            if row is None:
                raise KeyError("Dataset version not found")
            return {**self._metadata(row), **json.loads(row["snapshot"])}

    def run(
        self, dataset: str, version: int, *, mode: Mode = "keyword",
        rerank: bool = True, token_budget: int = 1200,
    ) -> dict:
        config = RunConfig(
            dataset=dataset, version=version, mode=mode, rerank=rerank, token_budget=token_budget
        )
        snapshot = self.get_dataset(config.dataset, config.version)
        metrics, cases = _score(snapshot, config)
        report = {
            "id": uuid4().hex, "created_at": _now(), "kind": "offline_retrieval",
            "config": config.model_dump(), "dataset_digest": snapshot["digest"],
            "metrics": metrics, "cases": cases,
        }
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs (id, name, version, created_at, report) VALUES (?, ?, ?, ?, ?)",
                (report["id"], dataset, version, report["created_at"], _json(report)),
            )
        return report

    def get_run(self, run_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT report FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError("Evaluation run not found")
            return json.loads(row["report"])

    def list_runs(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT report FROM runs ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
            )
            return [
                {key: value for key, value in json.loads(row["report"]).items() if key != "cases"}
                for row in rows
            ]

    def compare(self, baseline: str, candidate: str) -> dict:
        before = self.get_run(baseline)
        after = self.get_run(candidate)
        keys = ("dataset", "version")
        if (
            before["dataset_digest"] != after["dataset_digest"]
            or any(before["config"][key] != after["config"][key] for key in keys)
        ):
            raise ValueError("Only runs against the same dataset version can be compared")
        return {
            "baseline": baseline, "candidate": candidate, "dataset_digest": before["dataset_digest"],
            "delta": {
                key: round(value - before["metrics"][key], 6) if value is not None else None
                for key, value in after["metrics"].items() if key != "cases"
            },
            "changed_cases": [
                {"id": old["id"], "before": old["passed"], "after": new["passed"]}
                for old, new in zip(before["cases"], after["cases"])
                if old["passed"] != new["passed"]
            ],
        }
