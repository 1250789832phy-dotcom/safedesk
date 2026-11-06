from evaluation.run import run_evaluation


def test_evaluation_records_all_required_cases_and_real_outcomes():
    report = run_evaluation()

    assert len(report["cases"]) >= 6
    assert all(
        {"input", "expected", "actual", "passed", "failure_layer",
         "duration_ms", "model_calls", "cost"}
        <= case.keys()
        for case in report["cases"]
    )
    assert {case["id"] for case in report["cases"]} >= {
        "read_only",
        "clarification",
        "rag_hit",
        "rag_miss",
        "tool_rejection",
        "test_failure",
    }
    assert all(case["cost"] == "unknown" for case in report["cases"])


def test_evaluation_does_not_claim_mock_results_are_real_model_calls():
    report = run_evaluation()

    assert report["mode"] == "mock"
    assert all(case["model_calls"] == 0 for case in report["cases"])
    assert all(case["passed"] for case in report["cases"])
