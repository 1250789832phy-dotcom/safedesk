# Real Model Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run SafeDesk evaluation through the existing OpenAI-compatible Responses planner, count actual model requests, and persist a redacted JSON report without storing credentials.

**Architecture:** Keep `OpenAIResponsesPlanner` as the real planner and add an internal request counter incremented at the single `_generate()` boundary. `evaluation.run` will construct that planner from environment variables, snapshot its counter per case, and write an explicit real-model report mode. The existing deterministic test suite will inject a fake planner/client; only a manually configured local environment may make external calls.

**Tech Stack:** Python 3.11+, pytest, existing `openai` Responses client, JSON, environment variables.

## Global Constraints

- Never write `OPENAI_API_KEY` to source control, JSON reports, logs, or exception text.
- Read `OPENAI_BASE_URL`, `OPENAI_MODEL`, and `OPENAI_API_KEY` only from the process environment.
- Use the existing Responses API path (`client.responses.create`), not Chat Completions.
- Do not weaken existing evaluation assertions to make a real-model report pass.
- CI remains offline/mock and must not require a secret.
- Real-model reports record actual calls and outcomes; they do not claim semantic quality beyond the observed cases.

### Task 1: Planner Request Counting

**Files:**
- Modify: `agent/planner.py`
- Test: `tests/test_planner.py`

**Interfaces:**
- `OpenAIResponsesPlanner.model_calls` is an integer property, initially `0`.
- Every successful or failed invocation of `_generate()` increments `model_calls` exactly once.
- Existing redaction and error behavior remain unchanged.

- [x] **Step 1: Write the failing tests**

Add tests using a fake Responses client:

```python
def test_responses_planner_counts_generate_requests():
    planner = OpenAIResponsesPlanner(FakeClient(), "test-model", api_key="secret")
    planner._generate("one")
    planner._generate("two")
    assert planner.model_calls == 2


def test_responses_planner_counts_failed_request_without_leaking_key():
    planner = OpenAIResponsesPlanner(FailingClient(), "test-model", api_key="secret")
    with pytest.raises(RuntimeError) as error:
        planner._generate("request")
    assert planner.model_calls == 1
    assert "secret" not in str(error.value)
```

- [x] **Step 2: Run the tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_planner.py -q
```

Expected: the new assertions fail because `model_calls` does not exist or remains zero.

- [x] **Step 3: Implement the minimal counter**

Initialize `self.model_calls = 0` in `OpenAIResponsesPlanner.__init__`, and increment it as the first statement of `_generate()`. Do not increment in `plan()` or `finalize()` separately, because each delegates to `_generate()`.

- [x] **Step 4: Run the focused tests**

Run the same command. Expected: all planner tests pass.

### Task 2: Real Evaluation Construction and Per-Case Accounting

**Files:**
- Modify: `evaluation/run.py`
- Test: `tests/test_evaluation.py`

**Interfaces:**
- `run_evaluation()` selects `OpenAIResponsesPlanner.from_environment()` only when `SAFE_DESK_MODEL=openai`; missing configuration raises a clear error before any report is written.
- `_service_for(case_id, documents, planner)` uses the supplied planner for all ordinary cases; the existing unknown-tool case keeps only its deterministic tool contract while using the real planner.
- Each case stores `model_calls` as the delta of the shared planner counter before and after `service.submit()`.
- Top-level report includes `"mode": "openai_responses"` for real runs and the count of all model calls.

- [x] **Step 1: Write failing tests**

Add tests with a counting fake planner and environment variables:

```python
def test_evaluation_records_per_case_model_calls(monkeypatch):
    planner = CountingPlanner()
    report = run_evaluation(planner=planner)
    assert report["mode"] == "injected"
    assert all(case["model_calls"] == 2 for case in report["cases"])


def test_real_mode_requires_environment_key(monkeypatch):
    monkeypatch.setenv("SAFE_DESK_MODEL", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        run_evaluation()
```

- [x] **Step 2: Run tests to verify the missing behavior**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_evaluation.py -q
```

Expected: failures for the missing planner injection and zero model-call accounting.

- [x] **Step 3: Implement planner selection and accounting**

Add an optional `planner=None` parameter for tests. When omitted, select the real planner from environment for `SAFE_DESK_MODEL=openai`; otherwise preserve the mock path. Snapshot `getattr(planner, "model_calls", 0)` around each case. Keep the report JSON serializable and avoid including environment values.

- [x] **Step 4: Run focused evaluation tests**

Run the same command. Expected: all tests pass, with no network call because the tests inject a fake planner.

### Task 3: Safe Report Persistence and Real Run

**Files:**
- Modify: `evaluation/run.py`, `README.md`
- Modify: `data/evaluation.json`
- Test: `tests/test_evaluation.py`

- [x] **Step 1: Add report safety assertions**

Assert that serialized reports contain no `OPENAI_API_KEY`, `Authorization`, or configured key value, while retaining `mode`, `model_calls`, `duration_ms`, `actual`, `passed`, and `cost`.

- [x] **Step 2: Run the safety tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_evaluation.py -q
```

- [x] **Step 3: Update documentation**

Document the Responses API environment setup for a relay service, explain that the key remains shell-only, and distinguish a real report from the deterministic CI mock report.

- [x] **Step 4: Run a real local evaluation**

With the user-provided environment variables already set:

```powershell
.\.venv\Scripts\python.exe -m evaluation.run
```

Verify the command exits according to the observed report, `model_calls` is nonzero, and the JSON contains no key or authorization header.

- [x] **Step 5: Run the complete offline suite**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: all existing tests pass without requiring a network or secret.

- [ ] **Step 6: Commit the implementation and report**

```powershell
git add agent/planner.py evaluation/run.py tests/test_planner.py tests/test_evaluation.py README.md data/evaluation.json docs/superpowers/plans/2026-11-22-real-model-evaluation.md
git commit -m "feat: record real model evaluation calls safely"
```

The commit must use the repository identity `phy <299836398+1250789832phy-dotcom@users.noreply.github.com>`.
