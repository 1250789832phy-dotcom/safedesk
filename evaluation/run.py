import json
import time
from pathlib import Path

from agent.models import Plan, ToolCall, TaskStatus
from agent.service import AgentService
from agent.tools import ToolRegistry
from context.index import ContextIndex


CASES = [
    {
        "id": "read_only",
        "input": "Read example.txt",
        "expected": "Task succeeds and reports the real workspace file content.",
    },
    {
        "id": "clarification",
        "input": "Please help me with this.",
        "expected": "Ambiguous request asks a clarification question.",
    },
    {
        "id": "rag_hit",
        "input": "退款 7 天规则",
        "expected": "Keyword fallback returns rules.md as a source.",
    },
    {
        "id": "rag_miss",
        "input": "zzqvqx unknown observability concept",
        "expected": "No source is fabricated when keyword retrieval misses.",
    },
    {
        "id": "tool_rejection",
        "input": "Run an unregistered delete_file tool.",
        "expected": "Unknown tool is rejected and task fails.",
    },
    {
        "id": "test_failure",
        "input": "Run tests",
        "expected": "A nonzero fixed-test result is reported as a task failure.",
    },
]


class UnknownToolPlanner:
    def plan(self, request):
        return Plan(
            summary="Try an unregistered tool",
            acceptance_criteria=["Unregistered tools must not execute"],
            tool_calls=[ToolCall(name="delete_file")],
        )

    def finalize(self, request, plan, observations):
        return "Unexpected finalization"


def run_evaluation():
    documents = {
        "rules.md": "# Refund\n退款申请需在购买后 7 天内提交。"
    }
    results = []
    for case in CASES:
        started = time.perf_counter()
        service = _service_for(case["id"], documents)
        record = service.submit(case["input"])
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        actual = _actual_for(case["id"], record)
        passed = _matches(case["id"], record)
        results.append(
            {
                **case,
                "actual": actual,
                "passed": passed,
                # 归因只在失败时才有意义：通过的用例写入 "none"，
                # 否则报告里会出现 passed=true 与 failure_layer=context 并存的矛盾记录。
                "failure_layer": "none" if passed else _failure_layer(case["id"], record),
                "duration_ms": duration_ms,
                "model_calls": 0,
                "cost": "unknown",
            }
        )
    return {
        "mode": "mock",
        "retrieval_mode": "keyword_fallback",
        "cases": results,
        "passed": sum(case["passed"] for case in results),
        "failed": sum(not case["passed"] for case in results),
    }


def _service_for(case_id, documents):
    index = ContextIndex.from_documents(documents)
    if case_id == "tool_rejection":
        return AgentService(planner=UnknownToolPlanner(), context_index=index)
    if case_id == "test_failure":
        tools = ToolRegistry()
        tools._run_tests = lambda arguments, timeout_seconds=30: {
            "command": ["fixed", "pytest"],
            "returncode": 1,
            "stdout": "1 failed",
            "stderr": "AssertionError: expected example",
        }
        return AgentService(tools=tools, context_index=index)
    return AgentService(context_index=index)


def _matches(case_id, record):
    if case_id == "read_only":
        return record.status == TaskStatus.SUCCEEDED and "这是练习文件" in record.report
    if case_id == "clarification":
        return record.status == TaskStatus.CLARIFYING and bool(record.clarification_question)
    if case_id == "rag_hit":
        return record.retrieval_mode == "keyword_fallback" and "rules.md" in record.context_sources
    if case_id == "rag_miss":
        return record.retrieval_mode == "keyword_fallback" and not record.context_sources
    if case_id == "tool_rejection":
        return record.status == TaskStatus.FAILED and record.trace[-1].error_category == "unknown_tool"
    if case_id == "test_failure":
        return record.status == TaskStatus.FAILED and record.trace[-1].error_category == "tests_failed"
    return False


def _actual_for(case_id, record):
    return {
        "status": record.status.value,
        "report": record.report,
        "retrieval_mode": record.retrieval_mode,
        "context_sources": record.context_sources,
        "tool_errors": [
            item.error_category for item in record.trace if item.error_category
        ],
    }


def _failure_layer(case_id, record):
    if case_id == "test_failure":
        return "testing"
    if case_id == "tool_rejection":
        return "tool"
    if case_id in {"rag_hit", "rag_miss"}:
        return "context"
    return "none"


def main():
    report = run_evaluation()
    output_path = Path(__file__).resolve().parents[1] / "data" / "evaluation.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
