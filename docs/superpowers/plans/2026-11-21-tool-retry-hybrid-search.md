# Tool Budgets and Local Retrieval Implementation Plan

> Execute inline in the current session using test-driven development.

**Goal:** Bound tool waits and logical calls and provide offline vector/hybrid search.

**Architecture:** Keep ToolResult validation behavior, add ToolError for exhausted
execution budgets, and handle it at existing callers. Extend ContextIndex without
changing its default keyword retrieval mode.

**Tech Stack:** Python 3.11+, concurrent.futures, time, zlib, math, pytest.

## Constraints

- Work in E:\科研\safedesk on codex/tool-retry-hybrid-search.
- No new runtime dependencies or real model calls.
- Preserve all baseline checks except the requested embedding result label.
- An unfinished threaded tool must not be duplicated after a timeout.

## Task 1: Tool Execution

- [x] Add tests/test_tool_limits.py covering a sleep(2) tool with a 0.1s
  timeout, two OSError failures then success, exact backoff, exhaustion,
  no retry for validation/unknown tool/permission failures, 25-call limit,
  reset between tasks, idempotency and structured service/API failure.
- [x] Run `.venv\Scripts\python.exe -m pytest tests/test_tool_limits.py -q`
  and confirm failures identify missing ToolError/retry/budget behavior.
- [x] Implement ToolError, timeout configuration, reset_calls, validation,
  execute_inner and _call_with_retry in agent/tools.py.
- [x] Catch ToolError in agent/service.py and apps/api/main.py, preserving trace
  categories and the fixed-test subprocess's remaining timeout.
- [x] Run new tests and tests/test_agent.py and tests/test_changes.py.

## Task 2: Local Retrieval

- [x] Add tests/test_local_retrieval.py for deterministic 256-dimensional unit
  vectors, casefolding, empty input, all factory defaults, explicit None,
  hybrid retrieval including a keyword miss, ranking formula and top five.
- [x] Run `.venv\Scripts\python.exe -m pytest tests/test_local_retrieval.py -q`
  and confirm failures identify absent hash embedding/hybrid behavior.
- [x] Implement _hash_embedding, default providers and hybrid ranking in
  context/index.py. Update the existing mode label expectation in
  tests/test_context.py to local_hash_embedding.
- [x] Run both context test files and update README retrieval/tool limitations.

## Task 3: Verification

- [x] Run `.venv\Scripts\python.exe -m pytest -q` and inspect every failure.
- [x] Run run_evaluation() in memory; require six passed and zero failed.
- [x] Run git diff --check and review changes against the design.
- [x] Record evidence and remaining limits in D:\agent 学习\学习交接.md.

## Evidence

- Baseline: 117 passed in 8.27s.
- Initial new tests: 14 failed, 4 passed, identifying absent features.
- Existing exact subprocess timeout check caught a forwarding regression;
  corrected without weakening its assertion.
- Concurrent repair regression failed before locking reset+execute, then passed
  after the fix. Read-only follow-up review confirmed the budget bypass closed.
- Final complete suite: 138 passed in 15.38s (117 baseline plus 21 new).
- In-memory evaluation: 6 passed, 0 failed, 0 model calls.
- No real provider calls, extra dependencies or test assertion removals.
- Existing embedding result label assertion intentionally changed as requested.
- Changes remain on the local feature branch; remote CI has not been triggered.
