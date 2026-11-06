from pathlib import Path
import secrets
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from agent.changes import evaluate_gate
from agent.models import RepairSuggestion, TaskRequest
from agent.service import AgentService
from agent.settings import ModelSettings, SETTINGS_PATH
from apps.api.settings import register_settings


class ChangeProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=300)
    content: str = Field(max_length=100_000)


class ApplyConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool
    confirmation_token: str = Field(min_length=1, max_length=200)


def create_app(
    service: AgentService | None = None, *, settings_path=None, planner_factory=None,
) -> FastAPI:
    agent_service = service or AgentService.from_environment()
    app = FastAPI(title="SafeDesk Agent")
    settings = ModelSettings(
        agent_service,
        path=settings_path if settings_path is not None else SETTINGS_PATH,
        factory=planner_factory,
        load=service is None or settings_path is not None,
    )
    register_settings(app, settings)
    repair_limit = 2

    def run_fixed_tests():
        result = agent_service.tools.execute(
            "run_tests",
            {},
            timeout_seconds=agent_service.max_duration_seconds,
        )
        data = result.data if isinstance(result.data, dict) else {}
        failure_summary = result.message if not result.ok else ""
        return result, data, failure_summary

    def repair_view(repair_id: str, session: dict[str, object]):
        proposal = session.get("proposal")
        return {
            "repair_id": repair_id,
            "status": session["status"],
            "attempt": session["attempt"],
            "attempt_limit": repair_limit,
            "test_result": session["test_result"],
            "failure_summary": session["failure_summary"],
            "summary": session.get("summary"),
            "path": proposal.target if proposal else None,
            "diff": proposal.diff if proposal else None,
            "confirmation_token": proposal.confirmation_token if proposal else None,
            "change_id": session.get("change_id"),
            "stopped_reason": session.get("stopped_reason"),
        }

    def prepare_repair(repair_id: str, session: dict[str, object]):
        with settings.lock:
            _prepare_repair(repair_id, session)

    def _prepare_repair(repair_id: str, session: dict[str, object]):
        if session["attempt"] >= repair_limit:
            session["status"] = "blocked"
            session["stopped_reason"] = "repair_limit"
            return
        repairer = getattr(agent_service.planner, "propose_repair", None)
        if repairer is None:
            session["status"] = "blocked"
            session["stopped_reason"] = "repair_adapter_unavailable"
            return
        try:
            suggestion = RepairSuggestion.model_validate(
                repairer(str(session["failure_summary"]))
            )
            proposal = agent_service.change_manager.propose(
                suggestion.path, suggestion.content
            )
        except Exception as error:
            session["status"] = "blocked"
            session["stopped_reason"] = f"repair_proposal_error: {error}"
            return
        session["attempt"] = int(session["attempt"]) + 1
        session["proposal"] = proposal
        session["summary"] = suggestion.summary
        session["status"] = "waiting_confirmation"

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def workbench():
        return FileResponse(
            Path(__file__).resolve().parents[2] / "apps" / "web" / "index.html"
        )

    @app.post("/tasks", status_code=201)
    def create_task(
        request: TaskRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        with settings.lock:
            return agent_service.submit(
                request.task,
                idempotency_key,
                request.output_preference,
            ).model_dump(mode="json")

    @app.get("/tasks")
    def list_tasks():
        return [item.model_dump(mode="json") for item in agent_service.list()]

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str):
        record = agent_service.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return record.model_dump(mode="json")

    @app.post("/changes/proposals", status_code=201)
    def propose_change(request: ChangeProposalRequest):
        try:
            proposal = agent_service.change_manager.propose(
                request.path, request.content
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return {
            "path": proposal.target,
            "diff": proposal.diff,
            "confirmation_token": proposal.confirmation_token,
        }

    @app.post("/changes/proposals/{token}/apply")
    def apply_change(token: str, confirmation: ApplyConfirmation):
        if not secrets.compare_digest(token, confirmation.confirmation_token):
            raise HTTPException(
                status_code=409,
                detail="Confirmation token does not match the reviewed proposal.",
            )
        result = agent_service.change_manager.apply(
            token, confirmed=confirmation.confirmed
        )
        if result.error_category:
            raise HTTPException(
                status_code=409,
                detail={
                    "category": result.error_category,
                    "message": result.message,
                },
            )
        return {
            "applied": result.applied,
            "change_id": result.change_id,
            "backup_path": result.backup_path,
            "message": result.message,
        }

    @app.post("/changes/{change_id}/rollback")
    def rollback_change(change_id: str):
        result = agent_service.change_manager.rollback(change_id)
        if result.error_category:
            raise HTTPException(
                status_code=409,
                detail={
                    "category": result.error_category,
                    "message": result.message,
                },
            )
        return {"rolled_back": result.rolled_back, "message": result.message}

    @app.post("/repairs", status_code=201)
    def start_repair():
        test_result, data, failure_summary = run_fixed_tests()
        repair_id = str(uuid4())
        session: dict[str, object] = {
            "status": "succeeded" if test_result.ok else "running",
            "attempt": 0,
            "test_result": data,
            "failure_summary": failure_summary,
        }
        agent_service.repair_sessions[repair_id] = session
        if not test_result.ok:
            prepare_repair(repair_id, session)
        return repair_view(repair_id, session)

    @app.post("/repairs/{repair_id}/confirm")
    def confirm_repair(repair_id: str, confirmation: ApplyConfirmation):
        session = agent_service.repair_sessions.get(repair_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Repair session not found")
        if session["status"] != "waiting_confirmation":
            raise HTTPException(
                status_code=409,
                detail=f"Repair session is {session['status']}.",
            )
        proposal = session["proposal"]
        if not secrets.compare_digest(
            confirmation.confirmation_token,
            proposal.confirmation_token,
        ):
            raise HTTPException(
                status_code=409,
                detail="Confirmation token does not match the current diff.",
            )
        if not confirmation.confirmed:
            session["status"] = "blocked"
            session["stopped_reason"] = "confirmation_declined"
            return repair_view(repair_id, session)
        applied = agent_service.change_manager.apply(
            proposal.confirmation_token,
            confirmed=True,
        )
        if not applied.applied:
            session["status"] = "blocked"
            session["stopped_reason"] = applied.error_category or "apply_failed"
            return repair_view(repair_id, session)
        session["change_id"] = applied.change_id
        test_result, data, failure_summary = run_fixed_tests()
        session["test_result"] = data
        session["failure_summary"] = failure_summary
        session["status"] = "succeeded" if test_result.ok else "running"
        session.pop("proposal")
        if not test_result.ok:
            prepare_repair(repair_id, session)
        return repair_view(repair_id, session)

    @app.post("/checks/publish-gate")
    def publish_gate(
        returncode: int | None,
        test_count: int,
        execution_error: bool = False,
        unresolved_outside_change: bool = False,
    ):
        result = evaluate_gate(
            returncode,
            test_count,
            execution_error,
            unresolved_outside_change,
        )
        return {
            "passed": result.passed,
            "reasons": result.reasons,
            "report": (
                "Local checks passed; this endpoint does not publish or deploy."
                if result.passed
                else "Blocked; required local checks are incomplete."
            ),
        }

    return app


app = create_app()
