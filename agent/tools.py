import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.models import TaskRecord, ToolResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT / "workspace"


class ToolError(RuntimeError):
    pass


class ReadFileArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=300)


class SearchCodeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolSpec:
    def __init__(self, name, description, argument_model, implementation):
        self.name = name
        self.description = description
        self.argument_model = argument_model
        self.implementation = implementation


class ToolRegistry:
    def __init__(self, specs=None):
        self._calls = 0
        self.max_calls = 25
        self.timeout_seconds = 10.0
        self.task_provider: Callable[[], list[TaskRecord]] = lambda: []
        self.specs = {
            spec.name: spec
            for spec in (
                specs
                if specs is not None
                else [
                    ToolSpec(
                        "read_file",
                        "Read a text file under the example workspace",
                        ReadFileArguments,
                        self._read_file,
                    ),
                    ToolSpec(
                        "list_tasks",
                        "List in-memory task summaries",
                        NoArguments,
                        self._list_tasks,
                    ),
                    ToolSpec(
                        "search_code",
                        "Search project Python and Markdown files",
                        SearchCodeArguments,
                        self._search_code,
                    ),
                    ToolSpec(
                        "run_tests",
                        "Run the fixed project pytest command",
                        NoArguments,
                        self._run_tests,
                    ),
                ]
            )
        }

    def execute(
        self,
        name: str,
        arguments: dict[str, object],
        timeout_seconds: float = 30,
    ) -> ToolResult:
        self._calls += 1
        if self._calls > self.max_calls:
            raise ToolError("达到最大工具调用步数")
        spec = self.specs.get(name)
        if spec is None:
            return ToolResult(
                tool_name=name,
                ok=False,
                message=f"Unknown tool: {name}",
                error_category="unknown_tool",
            )
        try:
            parsed = (
                arguments
                if spec.argument_model is dict
                else spec.argument_model.model_validate(arguments)
            )
        except ValidationError as error:
            return ToolResult(
                tool_name=name,
                ok=False,
                message=f"Invalid arguments: {error.errors()[0]['msg']}",
                error_category="invalid_arguments",
            )
        return self._call_with_retry(name, parsed, timeout_seconds=timeout_seconds)

    def reset_calls(self):
        self._calls = 0

    def _call_with_retry(self, name, arguments, *, timeout_seconds=30):
        deadline = time.monotonic() + max(0, timeout_seconds)
        executor = ThreadPoolExecutor(max_workers=1)
        future = None
        last_error = TimeoutError("Tool time budget exceeded")
        try:
            for attempt in range(3):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                if future is None:
                    future = executor.submit(
                        self.execute_inner,
                        name,
                        arguments,
                        timeout_seconds if attempt == 0 else remaining,
                    )
                try:
                    return future.result(
                        timeout=min(self.timeout_seconds, remaining)
                    )
                except (PermissionError, FileNotFoundError) as error:
                    return ToolResult(
                        tool_name=name,
                        ok=False,
                        message=f"{type(error).__name__}: {error}",
                        error_category="tool_error",
                    )
                except (TimeoutError, OSError) as error:
                    last_error = error
                    # A live worker cannot be cancelled; retry its wait, not its work.
                    if future.done() and future.exception() is not None:
                        future = None
                    if attempt < 2:
                        delay = 0.5 * 2 ** attempt
                        if deadline - time.monotonic() <= delay:
                            break
                        time.sleep(delay)
            raise ToolError(
                f"工具执行超时/失败，已重试 2 次: "
                f"{type(last_error).__name__}: {last_error}"
            ) from last_error
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def execute_inner(self, name, arguments, timeout_seconds=30) -> ToolResult:
        spec = self.specs[name]
        try:
            if name == "run_tests":
                data = self._run_tests(arguments, timeout_seconds)
            else:
                data = (
                    spec.implementation.run(arguments)
                    if hasattr(spec.implementation, "run")
                    else spec.implementation(arguments)
                )
            if name == "run_tests" and data["returncode"] != 0:
                output = "\n".join(
                    part for part in (data["stdout"], data["stderr"]) if part
                )
                return ToolResult(
                    tool_name=name,
                    ok=False,
                    message=(
                        f"Fixed test command exited {data['returncode']}: "
                        f"{output[-4000:]}"
                    ),
                    data=data,
                    error_category="tests_failed",
                )
            return ToolResult(tool_name=name, ok=True, message=str(data), data=data)
        except (TimeoutError, OSError):
            raise
        except Exception as error:
            return ToolResult(
                tool_name=name,
                ok=False,
                message=f"{type(error).__name__}: {error}",
                error_category="tool_error",
            )

    def _read_file(self, arguments: ReadFileArguments):
        target = (WORKSPACE_ROOT / arguments.path).resolve()
        try:
            target.relative_to(WORKSPACE_ROOT.resolve())
        except ValueError as error:
            raise PermissionError("Path is outside the approved workspace") from error
        if not target.is_file():
            raise FileNotFoundError("Workspace file does not exist")
        if target.suffix.lower() not in {".txt", ".md", ".py", ".json"}:
            raise PermissionError("File type is not allowed")
        if target.stat().st_size > 100_000:
            raise ValueError("File exceeds the 100 KB read limit")
        return target.read_text(encoding="utf-8-sig")

    def _list_tasks(self, arguments: NoArguments):
        return [item.model_dump(mode="json") for item in self.task_provider()]

    def _search_code(self, arguments: SearchCodeArguments):
        matches = []
        for path in PROJECT_ROOT.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".py", ".md"}:
                continue
            if any(
                part
                in {
                    ".venv",
                    "__pycache__",
                    ".pytest_cache",
                    ".git",
                    "data",
                    "logs",
                }
                for part in path.parts
            ):
                continue
            try:
                lines = path.read_text(encoding="utf-8-sig").splitlines()
            except (OSError, UnicodeError):
                continue
            for line_number, line in enumerate(lines, start=1):
                if arguments.query.casefold() in line.casefold():
                    matches.append(
                        {
                            "path": path.relative_to(PROJECT_ROOT).as_posix(),
                            "line": line_number,
                            "text": line[:300],
                        }
                    )
                    if len(matches) >= 100:
                        return matches
        return matches

    def _run_tests(self, arguments: NoArguments, timeout_seconds: float = 30):
        command = [
            sys.executable,
            "-m",
            "pytest",
            "tests",
            "-q",
            "-p",
            "no:cacheprovider",
        ]
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=max(0.01, min(30, timeout_seconds)),
            check=False,
        )
        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-8000:],
            "stderr": completed.stderr[-8000:],
        }
