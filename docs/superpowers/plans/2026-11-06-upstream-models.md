# Upstream Model Discovery Implementation Plan

**Goal:** Explicitly fetch model IDs from the configured upstream and let the
user choose one in the Chinese settings form without typing a model first.

**Architecture:** POST a credentials-only body to the local settings endpoint;
resolve a blank key only against the same saved base URL. Use the installed
OpenAI SDK's `client.models.list()` with the existing client transport policy.
Return only bounded, validated, deduplicated model IDs. No settings write,
planner change, inference request, project context, or automatic fetch.

**Tech Stack:** Existing FastAPI, Pydantic, OpenAI SDK, plain HTML/JS, pytest,
and locally available Playwright/Edge. No new application dependencies.

## Constraints

- Preserve existing exercises, user settings and two baseline test failures.
- Reuse HTTPS/local HTTP validation, no redirects, no environment proxy,
  20-second timeout and zero retries.
- Require local same-origin/custom-header checks; sanitize validation/provider
  failures. Never return a key, upstream error body, or upstream metadata.
- Upstream `/models` output is a catalog, not proof of inference permission or
  compatibility with the selected task protocol.
- Keep manual model entry available when discovery fails, is empty or unsupported.
- Never stage, commit, create a repository, push, or use real saved credentials
  for automated verification.

## File Map

- Modify `agent/settings.py`: extract reusable credentials/client creation;
  add nonpersistent `discover_models(credentials)` with result cap.
- Modify `apps/api/settings.py`: POST `/settings/model/models`, redacted errors.
- Modify `apps/web/index.html`: explicit fetch command and native model select.
- Add `tests/test_upstream_models.py`: SDK MockTransport and API boundary tests.
- Modify `README.md`: UI steps and provider/permission limitations.
- Add `docs/2026-11-06-upstream-models-report.md`: actual execution evidence.
- Browser harness/screenshots stay in ignored `data/`.

## API Contract

Request: `POST /settings/model/models`, JSON fields `base_url` and optional
`api_key`; no model or protocol required. Existing settings write header required.

Response: `models: list[str]`, `truncated: bool`, `message: str`.
Sort unique valid IDs, return at most 2000, filter invalid IDs or IDs containing
the submitted/resolved key. Do not follow provider pagination automatically.
Bad local configuration is 422; missing reusable key is 400; provider HTTP
401/403/404/429/5xx, timeout, redirect, or bad schema is a sanitized 502.
An empty valid list is 200 with an honest empty state.

## Execution

- [x] Read current code and run baseline: 95 passed, 2 failed, exit 1.
- [x] Add failing API/UI tests before implementation: 17 failed, 2 passed.
- [x] Implement credentials/client reuse, discovery and endpoint; pass focused tests.
- [x] Add native select; fetch validates URL only, no required-model deadlock.
  Preserve a manually entered model until user chooses; require save to activate.
  Invalidate fetched options on URL/key edits; clear stale results on fetch errors.
  Disable controls during requests and restore valid disabled states afterward.
- [x] Run full settings tests and project suite: 46 passed; full 115 passed,
  same 2 baseline failures.
- [x] Browser-check local fake upstream with dummy credentials in an isolated
  app instance. Confirm GET `/models` authorization, selection/save, blank-key
  reuse, errors/empty states, no auto-fetch and 1280/375/320px layouts.
- [x] Review changes and update README/report with real results and limitations.
  Review found a stale catalog after restoring saved credentials; reproduced
  before the fix and verified afterward. Browser checks now pass 12 groups.
- [x] Restart only the verified live SafeDesk process, backing up current in-memory
  task history first. Do not overwrite the user's existing model configuration.
  Live port 8000 serves the fetch control and registered route; saved configuration
  SHA256 is unchanged. Latest focused suite: 46 passed; full: 115 passed, 2 baseline
  failures. See the incremental report for commands, evidence and remaining gaps.

## Commands

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_upstream_models.py -q -p no:cacheprovider --tb=short
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_model_settings.py tests/test_upstream_models.py -q -p no:cacheprovider --tb=short
.\.venv\Scripts\python.exe -X utf8 -m pytest tests -q -p no:cacheprovider --tb=short
```

No clean-environment installation or real-model permission verification is
included in this incremental feature. This is implementation evidence, not
evidence that the user has independently mastered this code.
