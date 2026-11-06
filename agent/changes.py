import difflib
import hashlib
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from uuid import uuid4

from agent.tools import PROJECT_ROOT, WORKSPACE_ROOT


MAX_FILE_BYTES = 100_000
ALLOWED_SUFFIXES = {".py", ".md", ".txt", ".json"}


@dataclass
class ChangeProposal:
    target: str
    diff: str
    confirmation_token: str
    _path: Path = field(repr=False)
    _original: str = field(repr=False)
    _replacement: str = field(repr=False)
    _original_hash: str = field(repr=False)
    used: bool = field(default=False, repr=False)


@dataclass
class ChangeResult:
    applied: bool = False
    rolled_back: bool = False
    change_id: str | None = None
    backup_path: str | None = None
    error_category: str | None = None
    message: str = ""


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reasons: list[str]


@dataclass(frozen=True)
class RepairResult:
    attempts: int
    succeeded: bool
    stopped_reason: str | None


class ChangeManager:
    def __init__(
        self,
        workspace: Path = WORKSPACE_ROOT,
        backup_root: Path | None = None,
    ):
        self.workspace = workspace.resolve()
        self.backup_root = (backup_root or PROJECT_ROOT / "data" / "backups").resolve()
        self.proposals: dict[str, ChangeProposal] = {}
        self.applied: dict[str, dict[str, object]] = {}

    def propose(self, relative_path: str, replacement: str) -> ChangeProposal:
        path = self.workspace / relative_path
        path_error = self._validate_path(path, allow_missing=True)
        if path_error:
            raise ValueError(path_error)
        current = ""
        try:
            if path.is_file():
                current = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            current = ""
        diff = "".join(
            difflib.unified_diff(
                current.splitlines(keepends=True),
                replacement.splitlines(keepends=True),
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
            )
        )
        token = secrets.token_urlsafe(24)
        proposal = ChangeProposal(
            target=relative_path,
            diff=diff,
            confirmation_token=token,
            _path=path,
            _original=current,
            _replacement=replacement,
            _original_hash=_digest(current),
        )
        self.proposals[token] = proposal
        return proposal

    def apply(
        self, confirmation_token: str, confirmed: bool = False
    ) -> ChangeResult:
        proposal = self.proposals.get(confirmation_token)
        if proposal is None:
            return ChangeResult(
                error_category="unknown_confirmation",
                message="No pending proposal matches this confirmation token.",
            )
        if proposal.used:
            return ChangeResult(
                error_category="confirmation_used",
                message="This confirmation token has already been used.",
            )
        if not confirmed:
            return ChangeResult(
                error_category="confirmation_required",
                message="Explicit user confirmation is required before writing.",
            )
        proposal.used = True
        path_error = self._validate_path(proposal._path, allow_missing=True)
        if path_error:
            return ChangeResult(
                error_category="path_not_allowed",
                message=path_error,
            )
        if proposal._path.exists():
            if not proposal._path.is_file():
                return ChangeResult(
                    error_category="not_a_file",
                    message="Target must be a regular file.",
                )
            if proposal._path.stat().st_size > MAX_FILE_BYTES:
                return ChangeResult(
                    error_category="file_too_large",
                    message="Target exceeds the 100 KB modification limit.",
                )
            current = proposal._path.read_text(encoding="utf-8")
        else:
            current = ""
        if _digest(current) != proposal._original_hash:
            return ChangeResult(
                error_category="file_changed",
                message="Target changed after proposal; create a new diff.",
            )

        self.backup_root.mkdir(parents=True, exist_ok=True)
        change_id = str(uuid4())
        backup_path = self.backup_root / f"{change_id}.bak"
        backup_path.write_text(proposal._original, encoding="utf-8")
        proposal._path.parent.mkdir(parents=True, exist_ok=True)
        proposal._path.write_text(proposal._replacement, encoding="utf-8")
        self.applied[change_id] = {
            "path": proposal._path,
            "backup": backup_path,
            "applied_hash": _digest(proposal._replacement),
            "rolled_back": False,
        }
        return ChangeResult(
            applied=True,
            change_id=change_id,
            backup_path=str(backup_path),
            message="Confirmed change applied.",
        )

    def rollback(self, change_id: str) -> ChangeResult:
        change = self.applied.get(change_id)
        if change is None:
            return ChangeResult(
                error_category="unknown_change",
                message="No applied change matches this id.",
            )
        if change["rolled_back"]:
            return ChangeResult(
                error_category="already_rolled_back",
                message="This change has already been rolled back.",
            )
        path = change["path"]
        path_error = self._validate_path(path, allow_missing=False)
        if path_error:
            return ChangeResult(
                error_category="path_not_allowed",
                message=path_error,
            )
        current = path.read_text(encoding="utf-8")
        if _digest(current) != change["applied_hash"]:
            return ChangeResult(
                error_category="file_changed",
                message="File changed after apply; rollback would overwrite user work.",
            )
        path.write_text(Path(change["backup"]).read_text(encoding="utf-8"), encoding="utf-8")
        change["rolled_back"] = True
        return ChangeResult(
            rolled_back=True,
            change_id=change_id,
            message="Change rolled back.",
        )

    def _validate_path(self, path: Path, allow_missing: bool) -> str | None:
        try:
            relative = path.relative_to(self.workspace)
        except ValueError:
            return "Target is outside the approved workspace."
        # Symlink detection must run before path resolution: resolving first would
        # follow the link out of the workspace and report a workspace escape for
        # what is actually a rejected symlink write.
        cursor = self.workspace
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                return "Symbolic links are not allowed for writes."
        try:
            resolved = path.resolve(strict=not allow_missing)
            resolved.relative_to(self.workspace)
        except (ValueError, OSError):
            return "Target is outside the approved workspace."
        if ".." in relative.parts:
            return "Parent-directory traversal is not allowed."
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            return "File type is not allowed for writes."
        return None


def evaluate_gate(
    returncode: int | None,
    test_count: int,
    execution_error: bool = False,
    unresolved_outside_change: bool = False,
) -> GateResult:
    reasons = []
    if execution_error:
        reasons.append("execution_error")
    if returncode is None:
        reasons.append("tests_not_run")
    elif returncode != 0:
        reasons.append("tests_failed")
    if test_count <= 0:
        reasons.append("zero_tests")
    if unresolved_outside_change:
        reasons.append("unresolved_outside_change")
    return GateResult(passed=not reasons, reasons=reasons)


def run_bounded_repairs(
    attempt: Callable[[], bool], max_attempts: int
) -> RepairResult:
    if max_attempts < 0:
        raise ValueError("max_attempts must be non-negative")
    for index in range(max_attempts):
        if attempt():
            return RepairResult(index + 1, True, None)
    return RepairResult(max_attempts, False, "repair_limit")


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
