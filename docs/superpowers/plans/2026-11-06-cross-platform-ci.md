# Cross-Platform CI Implementation Plan

**Goal:** Run core tests on Linux and Windows while keeping real DPAPI coverage on Windows.

**Architecture:** Keep application encryption unchanged. Apply per-test `skipif` markers to the nine tests that save real encrypted settings. Keep all other tests active on both systems.

**Tech Stack:** pytest, GitHub Actions, Python 3.11/3.12/3.13.

## Constraints

- Do not mock encryption in the committed persistence tests.
- Do not delete local temporary directories.
- Do not claim Linux encrypted persistence support.

## Tasks

- [x] Add an assertion that the initial save returns 200. Confirm the strengthened different-upstream test fails under an injected `protect_key` error (`500 != 200`).
- [x] Add `windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Configuration storage uses Windows DPAPI (crypt32)")` in both settings-related test modules and decorate only tests that require successful encrypted persistence.
- [x] Add `os: [ubuntu-latest, windows-latest]` to the workflow matrix, use `runs-on: ${{ matrix.os }}`, and include `${{ matrix.os }}` in artifact names. Use `continue-on-error: true` for the existing best-effort evaluation step instead of shell-specific `|| true`.
- [x] Update README with the six-job matrix and Windows-only encrypted configuration limitation.
- [ ] Run the complete Windows suite and evaluation, inspect the diff, commit only scoped files, push, and verify all six remote CI jobs.
