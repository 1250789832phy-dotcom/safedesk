# Persistent Model Settings

**Goal:** Configure model URL, protocol, model name and API key in the Chinese
workbench; save locally and restore after restart.

**Architecture:** Keep the existing planner contract and registered tools.
Add a settings store at ignored `data/model-settings.json`. Encrypt API keys
using Windows current-user DPAPI; refuse persistence on unsupported systems.
Expose redacted status only. Save/apply and explicit network test are separate.

**Constraints:** No real keys in source, tests, logs, API responses or browser
storage. Do not overwrite existing exercises or fix unrelated baseline failures.
No commits or pushes. Keep the service loopback-only. Existing environment
configuration remains the fallback when there is no saved configuration.

## Acceptance

- [x] RED: settings endpoint/UI tests fail before implementation (18 failures).
- [x] Store settings atomically, round-trip encrypted key on this Windows user.
- [x] Load saved mode/configuration on actual server restart without a network call.
- [x] Support explicit Responses and Chat Completions protocol selection (fake HTTP transport).
- [x] GET/status and covered validation/network failures never return test API keys.
- [x] Prevent cross-origin settings updates; require a custom request header.
- [x] Blank key preserves a saved key only for the same normalized base URL.
- [x] Saving failed configuration preserves previous file and active planner.
- [x] Test connection sends only a tiny probe, no project context or tool action.
- [x] Chinese form: password reveal, save/apply, test with cost confirmation,
  mock switch, clear saved key/configuration, disabled/loading/error states.
- [x] Run complete tests and browser checks on desktop/mobile; report baseline failures.

## Results

- Focused suite: `26 passed in 3.14s`, exit 0.
- Full suite: `2 failed, 95 passed in 9.01s`, exit 1; same two baseline failures.
- Mock evaluation: `6 passed, 0 failed`, exit 0; zero real model calls.
- Settings browser: 8 prepare checks plus 3 post-restart checks; no page errors.
- Chinese UI browser smoke: 10 grouped checks; no page errors.
- Screenshots inspected at 1280px and 320px; layout checks also ran at 375px.
- Review found a probe parameter incompatibility; regression tests failed first,
  then passed after moving to bounded `max_completion_tokens` and handling a
  budget-exhausted reasoning response as interface connectivity only.
- Connection lifecycle regressions failed first, then passed after closing replaced
  or failed candidate clients. Repair planner access shares the settings lock.
- No real external model, microphone/STT/TTS service, or new clean installation
  was verified. No staging, commit, remote creation or push.
- Dummy browser configuration was cleared; live application is in mock mode.

## Verification

From the project directory:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_model_settings.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --tb=short
```

Baseline: 69 passed, 2 failed (health `busy` versus `ok`; symlink rejection
message). Adapter tests use an injected fake SDK transport. No real model
validation is claimed without the user's endpoint, key and model.
