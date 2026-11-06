# Chinese UI Implementation Plan

**Goal:** Apply the user-approved Simplified Chinese UI without changing layout,
API fields, backend behavior, or the SafeDesk name.

**Architecture:** Translate presentation text in `apps/web/index.html`. Map task
status codes to Chinese labels for the selected task and history, keeping raw
codes for CSS and API use. Preserve file contents, model output, JSON fields,
and diagnostic details verbatim.

**Tech Stack:** HTML, browser JavaScript, FastAPI TestClient, pytest.

## Constraints

- Preserve all existing user changes and learning exercises.
- Do not stage, commit, push, or change backend behavior for this UI request.
- Baseline: 43 passed, 2 failed (health status and symlink rejection message).
- Browser speech checks cover UI behavior only, not real speech services.

## Steps

- [x] Add `tests/test_web_ui.py` to parse served HTML and check Chinese labels,
  language metadata, empty states, and unchanged control IDs/option values.
- [x] Run the new tests and confirm failures against the English UI.
- [x] Translate static and dynamic UI text; add status and speech-error labels.
- [x] Run new tests and the complete suite; compare with baseline failures.
- [x] Check desktop/mobile rendering and dynamic states in a browser.
- [x] Verify the served page and report actual results and remaining failures.

Commands from the project directory:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_web_ui.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

## Actual Verification

- New HTML tests: 26 failed before implementation, then 26 passed.
- Final complete suite: 69 passed, 2 failed; both failures match the baseline.
- Browser verification: 10 grouped checks passed in installed Edge, no page
  errors. Includes all task status labels, structured HTTP error diagnostics,
  one actual local mock-planner read task, and 1280/375/320 px layouts.
- Speech error/recording checks use UI fixtures, not real STT/TTS.
- Served page: HTTP 200 with Chinese UI. No backend changes, commits, or pushes.
- Browser script and screenshots remain in Git-ignored `data/`.
