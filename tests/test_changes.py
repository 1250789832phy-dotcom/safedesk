from pathlib import Path

from agent.changes import ChangeManager, GateResult, evaluate_gate
from agent.service import AgentService
from apps.api.main import create_app
from fastapi.testclient import TestClient


def test_proposal_shows_diff_without_writing(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "sample.txt"
    target.write_text("before\n", encoding="utf-8")
    manager = ChangeManager(workspace)

    proposal = manager.propose("sample.txt", "after\n")

    assert target.read_text(encoding="utf-8") == "before\n"
    assert "-before" in proposal.diff
    assert "+after" in proposal.diff
    assert proposal.confirmation_token


def test_apply_requires_confirmation_and_backup(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "sample.txt"
    target.write_text("before\n", encoding="utf-8")
    manager = ChangeManager(workspace)
    proposal = manager.propose("sample.txt", "after\n")

    result = manager.apply(proposal.confirmation_token)

    assert result.applied is False
    assert target.read_text(encoding="utf-8") == "before\n"
    assert result.error_category == "confirmation_required"

    result = manager.apply(
        proposal.confirmation_token, confirmed=True
    )

    assert result.applied is True
    assert target.read_text(encoding="utf-8") == "after\n"
    assert Path(result.backup_path).read_text(encoding="utf-8") == "before\n"


def test_apply_rejects_workspace_escape_and_symlink(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    manager = ChangeManager(workspace)

    try:
        (workspace / "linked.txt").symlink_to(outside)
    except OSError:
        pass
    else:
        try:
            manager.propose("linked.txt", "changed")
        except ValueError as error:
            assert str(error) == "Symbolic links are not allowed for writes."
        else:
            raise AssertionError("A symbolic-link target must be rejected")
        assert outside.read_text(encoding="utf-8") == "private"

    try:
        manager.propose("../outside.txt", "changed")
    except ValueError as error:
        assert str(error) == "Target is outside the approved workspace."
    else:
        raise AssertionError("A path outside the workspace must be rejected")


def test_proposal_rejects_workspace_escape_before_reading_external_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("private contents", encoding="utf-8")
    manager = ChangeManager(workspace)

    try:
        manager.propose("../private.txt", "replacement")
    except ValueError as error:
        assert str(error) == "Target is outside the approved workspace."
    else:
        raise AssertionError("An external file must not produce a proposal")


def test_proposal_rejects_symlink_to_another_workspace_file(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.txt"
    target.write_text("original", encoding="utf-8")
    alias = workspace / "alias.txt"
    real_resolve = Path.resolve
    real_is_symlink = Path.is_symlink

    def resolve(path, *args, **kwargs):
        if path == alias:
            return target
        return real_resolve(path, *args, **kwargs)

    def is_symlink(path):
        if path == alias:
            return True
        return real_is_symlink(path)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    manager = ChangeManager(workspace)

    try:
        manager.propose("alias.txt", "replacement")
    except ValueError as error:
        assert str(error) == "Symbolic links are not allowed for writes."
    else:
        raise AssertionError("A symlink inside the workspace must be rejected")
    assert target.read_text(encoding="utf-8") == "original"


def test_apply_rejects_duplicate_confirmation(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "sample.txt"
    target.write_text("before", encoding="utf-8")
    manager = ChangeManager(workspace)
    proposal = manager.propose("sample.txt", "after")

    first = manager.apply(proposal.confirmation_token, confirmed=True)
    second = manager.apply(proposal.confirmation_token, confirmed=True)

    assert first.applied is True
    assert second.error_category == "confirmation_used"


def test_rollback_restores_only_if_applied_content_is_unchanged(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "sample.txt"
    target.write_text("before", encoding="utf-8")
    manager = ChangeManager(workspace)
    proposal = manager.propose("sample.txt", "after")
    result = manager.apply(proposal.confirmation_token, confirmed=True)

    assert manager.rollback(result.change_id).rolled_back is True
    assert target.read_text(encoding="utf-8") == "before"

    proposal = manager.propose("sample.txt", "again")
    result = manager.apply(proposal.confirmation_token, confirmed=True)
    target.write_text("user edit", encoding="utf-8")
    rollback = manager.rollback(result.change_id)

    assert rollback.error_category == "file_changed"
    assert target.read_text(encoding="utf-8") == "user edit"


def test_publish_gate_blocks_failed_error_zero_and_unresolved_changes():
    assert evaluate_gate(returncode=1, test_count=2).passed is False
    assert evaluate_gate(returncode=0, test_count=0).passed is False
    assert evaluate_gate(returncode=None, test_count=2).passed is False
    assert (
        evaluate_gate(
            returncode=0,
            test_count=2,
            unresolved_outside_change=True,
        ).passed
        is False
    )
    assert evaluate_gate(returncode=0, test_count=2).passed is True


def test_repair_loop_stops_at_configured_limit():
    from agent.changes import run_bounded_repairs

    attempts = []

    def repair():
        attempts.append("repair")
        return False

    result = run_bounded_repairs(repair, max_attempts=2)

    assert attempts == ["repair", "repair"]
    assert result.attempts == 2
    assert result.stopped_reason == "repair_limit"


def test_api_proposal_requires_a_separate_explicit_apply_confirmation(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sample.txt").write_text("before\n", encoding="utf-8")
    service = AgentService()
    service.change_manager = ChangeManager(
        workspace=workspace,
        backup_root=tmp_path / "backups",
    )
    client = TestClient(create_app(service))

    proposal_response = client.post(
        "/changes/proposals",
        json={"path": "sample.txt", "content": "after\n"},
    )
    proposal = proposal_response.json()
    proposal_url = f"/changes/proposals/{proposal['confirmation_token']}/apply"
    missing = client.post(proposal_url, json={"confirmed": True})
    mismatch = client.post(
        proposal_url,
        json={"confirmed": True, "confirmation_token": "wrong-token"},
    )

    assert missing.status_code == 422
    assert mismatch.status_code == 409
    assert (workspace / "sample.txt").read_text(encoding="utf-8") == "before\n"

    apply_response = client.post(
        proposal_url,
        json={
            "confirmed": True,
            "confirmation_token": proposal["confirmation_token"],
        },
    )
    replay = client.post(
        proposal_url,
        json={
            "confirmed": True,
            "confirmation_token": proposal["confirmation_token"],
        },
    )

    assert proposal_response.status_code == 201
    assert "-before" in proposal["diff"]
    assert apply_response.json()["applied"] is True
    assert replay.status_code == 409
    assert (workspace / "sample.txt").read_text(encoding="utf-8") == "after\n"


def test_api_rejects_external_proposal_without_returning_file_contents(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "private.txt").write_text("private contents", encoding="utf-8")
    service = AgentService()
    service.change_manager = ChangeManager(
        workspace=workspace,
        backup_root=tmp_path / "backups",
    )
    client = TestClient(create_app(service))

    response = client.post(
        "/changes/proposals",
        json={"path": "../private.txt", "content": "replacement"},
    )

    assert response.status_code == 400
    assert "private contents" not in response.text
    assert response.json()["detail"] == "Target is outside the approved workspace."


def test_repair_api_passes_test_failure_to_repairer_and_waits_for_confirmation(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "sample.txt"
    target.write_text("before\n", encoding="utf-8")
    service = AgentService()
    results = iter(
        [
            {
                "command": ["fixed", "pytest"],
                "returncode": 1,
                "stdout": "1 failed",
                "stderr": "AssertionError: expected after",
            },
            {
                "command": ["fixed", "pytest"],
                "returncode": 0,
                "stdout": "1 passed",
                "stderr": "",
            },
        ]
    )
    service.tools._run_tests = lambda arguments, timeout_seconds=30: next(results)
    received_failures = []

    def propose_repair(failure_summary):
        received_failures.append(failure_summary)
        return {
            "summary": "Update the sample fixture.",
            "path": "sample.txt",
            "content": "after\n",
        }

    service.planner.propose_repair = propose_repair
    service.change_manager = ChangeManager(
        workspace=workspace,
        backup_root=tmp_path / "backups",
    )
    client = TestClient(create_app(service))

    started = client.post("/repairs")
    session = started.json()

    assert started.status_code == 201
    assert session["status"] == "waiting_confirmation"
    assert "AssertionError: expected after" in received_failures[0]
    assert "-before" in session["diff"]
    assert target.read_text(encoding="utf-8") == "before\n"

    completed = client.post(
        f"/repairs/{session['repair_id']}/confirm",
        json={
            "confirmed": True,
            "confirmation_token": session["confirmation_token"],
        },
    )

    assert completed.status_code == 200
    assert completed.json()["status"] == "succeeded"
    assert target.read_text(encoding="utf-8") == "after\n"


def test_repair_api_stops_after_configured_attempt_limit(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "sample.txt"
    target.write_text("before\n", encoding="utf-8")
    service = AgentService()
    service.tools._run_tests = lambda arguments, timeout_seconds=30: {
        "command": ["fixed", "pytest"],
        "returncode": 1,
        "stdout": "1 failed",
        "stderr": "AssertionError",
    }
    proposals = iter(["first\n", "second\n"])
    service.planner.propose_repair = lambda failure_summary: {
        "summary": "Try a bounded correction.",
        "path": "sample.txt",
        "content": next(proposals),
    }
    service.change_manager = ChangeManager(
        workspace=workspace,
        backup_root=tmp_path / "backups",
    )
    client = TestClient(create_app(service))

    session = client.post("/repairs").json()
    assert session["attempt"] == 1

    first = client.post(
        f"/repairs/{session['repair_id']}/confirm",
        json={
            "confirmed": True,
            "confirmation_token": session["confirmation_token"],
        },
    ).json()
    assert first["status"] == "waiting_confirmation"
    assert first["attempt"] == 2

    final = client.post(
        f"/repairs/{session['repair_id']}/confirm",
        json={
            "confirmed": True,
            "confirmation_token": first["confirmation_token"],
        },
    ).json()
    assert final["status"] == "blocked"
    assert final["stopped_reason"] == "repair_limit"
    assert final["attempt"] == 2


def test_repair_confirmation_requires_current_diff_token_and_rejects_replay(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "sample.txt"
    target.write_text("before\n", encoding="utf-8")
    service = AgentService()
    service.tools._run_tests = lambda arguments, timeout_seconds=30: {
        "command": ["fixed", "pytest"],
        "returncode": 1,
        "stdout": "1 failed",
        "stderr": "AssertionError",
    }
    service.planner.propose_repair = lambda failure_summary: {
        "summary": "Propose a correction.",
        "path": "sample.txt",
        "content": "after\n",
    }
    service.change_manager = ChangeManager(
        workspace=workspace,
        backup_root=tmp_path / "backups",
    )
    client = TestClient(create_app(service))
    session = client.post("/repairs").json()
    confirm_url = f"/repairs/{session['repair_id']}/confirm"

    missing = client.post(confirm_url, json={"confirmed": True})
    wrong = client.post(
        confirm_url,
        json={"confirmed": True, "confirmation_token": "not-the-current-token"},
    )

    assert missing.status_code == 422
    assert wrong.status_code == 409
    assert target.read_text(encoding="utf-8") == "before\n"

    accepted = client.post(
        confirm_url,
        json={
            "confirmed": True,
            "confirmation_token": session["confirmation_token"],
        },
    )
    replay = client.post(
        confirm_url,
        json={
            "confirmed": True,
            "confirmation_token": session["confirmation_token"],
        },
    )

    assert accepted.status_code == 200
    assert replay.status_code == 409
