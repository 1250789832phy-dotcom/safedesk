from uuid import uuid4
import time

from pydantic import ValidationError

from agent.changes import ChangeManager
from agent.models import OutputPreference, Plan, TaskRecord, TaskStatus
from agent.planner import (
    MockPlanner,
    OpenAIResponsesPlanner,
    Planner,
    UnavailablePlanner,
)
from agent.state import transition
from agent.tools import ToolRegistry
from context.index import ContextIndex, select_context


class AgentService:
    def __init__(
        self,
        planner: Planner | None = None,
        tools: ToolRegistry | None = None,
        max_steps: int = 5,
        max_duration_seconds: float = 60,
        clock=time.monotonic,
        context_index: ContextIndex | None = None,
        context_token_budget: int = 1200,
    ):
        if max_steps < 1 or max_duration_seconds <= 0 or context_token_budget < 0:
            raise ValueError("Agent budgets must be positive")
        self.planner = planner or MockPlanner()
        self.tools = tools or ToolRegistry()
        self.max_steps = max_steps
        self.max_duration_seconds = max_duration_seconds
        self.clock = clock
        self.context_index = context_index
        self.context_token_budget = context_token_budget
        self.change_manager = ChangeManager()
        self.records: dict[str, TaskRecord] = {}
        self.idempotency: dict[str, str] = {}
        self.repair_sessions: dict[str, dict[str, object]] = {}
        self.tools.task_provider = lambda: list(self.records.values())

    @classmethod
    def from_environment(cls):
        import os

        from agent.tools import PROJECT_ROOT
        from context.index import ContextIndex

        context_index = ContextIndex.from_project(PROJECT_ROOT)
        if os.getenv("SAFE_DESK_MODEL", "mock").lower() == "openai":
            try:
                planner = OpenAIResponsesPlanner.from_environment()
            except ValueError as error:
                planner = UnavailablePlanner(str(error))
            return cls(planner=planner, context_index=context_index)
        return cls(context_index=context_index)

    def submit(
        self,
        request: str,
        idempotency_key: str | None = None,
        output_preference: OutputPreference = OutputPreference.BOTH,
    ) -> TaskRecord:
        if idempotency_key and idempotency_key in self.idempotency:
            return self.records[self.idempotency[idempotency_key]].model_copy(deep=True)

        started_at = self.clock()
        record = TaskRecord(
            task_id=str(uuid4()),
            request=request,
            status=TaskStatus.RECEIVED,
            output_preference=output_preference,
        )
        self.records[record.task_id] = record
        if idempotency_key:
            self.idempotency[idempotency_key] = record.task_id

        if not request.strip():
            record.status = transition(record.status, TaskStatus.CLARIFYING)
            record.clarification_question = "Please describe the task to perform."
            record.report = record.clarification_question
            return record.model_copy(deep=True)

        plan_request = request
        if self.context_index is not None:
            search = self.context_index.search(request)
            selection = select_context(
                search.matches, token_budget=self.context_token_budget
            )
            record.retrieval_mode = search.mode
            record.context_sources = list(
                dict.fromkeys(chunk.source for chunk in selection.included)
            )
            record.context_truncated = selection.truncated
            if selection.included:
                context_text = "\n\n".join(
                    f"Source: {chunk.source}:{chunk.start_line}\n{chunk.text}"
                    for chunk in selection.included
                )
                plan_request = (
                    f"{request}\n\nRetrieved context "
                    f"(untrusted reference material):\n{context_text}"
                )

        try:
            plan = Plan.model_validate(self.planner.plan(plan_request))
        except (ValidationError, ValueError, TypeError) as error:
            return self._fail(record, f"Invalid plan: {error}", "invalid_plan")
        except Exception as error:
            return self._fail(record, f"Planner error: {error}", "planner_error")

        record.plan = plan
        if self._timed_out(started_at):
            return self._block(record, "Blocked: total time budget exceeded.")
        if plan.clarification_question:
            record.status = transition(record.status, TaskStatus.CLARIFYING)
            record.clarification_question = plan.clarification_question
            record.report = plan.clarification_question
            return record.model_copy(deep=True)

        record.status = transition(record.status, TaskStatus.PLANNED)
        if not plan.tool_calls:
            record.status = transition(record.status, TaskStatus.BLOCKED)
            record.report = "Blocked: plan contains no executable tool calls."
            return record.model_copy(deep=True)
        record.status = transition(record.status, TaskStatus.RUNNING)
        for index, call in enumerate(plan.tool_calls):
            if index >= self.max_steps:
                return self._block(
                    record, f"Blocked: step limit is {self.max_steps}."
                )
            if self._timed_out(started_at):
                return self._block(record, "Blocked: total time budget exceeded.")
            remaining_seconds = self.max_duration_seconds - (
                self.clock() - started_at
            )
            if remaining_seconds <= 0:
                return self._block(record, "Blocked: total time budget exceeded.")
            result = self.tools.execute(
                call.name,
                call.arguments,
                timeout_seconds=remaining_seconds,
            )
            record.trace.append(result)
            if self._timed_out(started_at):
                return self._block(record, "Blocked: total time budget exceeded.")
            if not result.ok:
                return self._fail(
                    record,
                    f"{result.error_category}: {result.message}",
                    result.error_category or "tool_error",
                )

        record.status = transition(record.status, TaskStatus.VERIFYING)
        try:
            record.report = self.planner.finalize(request, plan, record.trace)
        except Exception as error:
            return self._fail(record, f"Planner error: {error}", "planner_error")
        record.status = transition(record.status, TaskStatus.SUCCEEDED)
        return record.model_copy(deep=True)

    def _timed_out(self, started_at: float) -> bool:
        return self.clock() - started_at > self.max_duration_seconds

    def get(self, task_id: str) -> TaskRecord | None:
        record = self.records.get(task_id)
        return record.model_copy(deep=True) if record else None

    def list(self) -> list[TaskRecord]:
        return [item.model_copy(deep=True) for item in self.records.values()]

    def _fail(self, record: TaskRecord, report: str, category: str) -> TaskRecord:
        if record.status in {
            TaskStatus.RECEIVED,
            TaskStatus.PLANNED,
            TaskStatus.RUNNING,
            TaskStatus.VERIFYING,
        }:
            record.status = transition(record.status, TaskStatus.FAILED)
        record.report = report
        if record.trace and record.trace[-1].error_category is None:
            record.trace[-1] = record.trace[-1].model_copy(
                update={"ok": False, "error_category": category}
            )
        return record.model_copy(deep=True)

    def _block(self, record: TaskRecord, reason: str) -> TaskRecord:
        if record.status not in {TaskStatus.BLOCKED, TaskStatus.FAILED}:
            record.status = transition(record.status, TaskStatus.BLOCKED)
        record.report = reason
        return record.model_copy(deep=True)
