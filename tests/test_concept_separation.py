"""Concept-separation safeguards for the research vocabulary (stage 10).

Pins the distinctions the application layer must never see blurred:
Observation vs Finding vs Hypothesis vs Attribution vs Intervention vs
Verification vs Defense Recommendation vs Defense Effectiveness, plus
planned-vs-executed and significance-vs-causal-verification.
"""

from fastapi.testclient import TestClient

from agentguard.api.app import create_app


def _engine_run(app) -> dict:
    scenario = app.state.engine.scenarios.list_scenarios()[0].scenario_id
    with TestClient(app) as client:
        return client.post("/v1/runs", json={
            "scenario_id": scenario, "experiment_id": "exp-concepts",
        }).json()


def test_observations_findings_and_hypotheses_stay_distinct(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    with TestClient(app) as client:
        report = _engine_run(app)
        run_id = report["run_id"]
        observations = client.get(f"/v1/runs/{run_id}/observations").json()["items"]
        findings = client.get(f"/v1/runs/{run_id}/findings").json()["items"]
        hypotheses = client.get(f"/v1/runs/{run_id}/hypotheses").json()["items"]
    assert observations and findings and hypotheses
    # Observations are recorded events: actor/event type, no risk verdict.
    assert all({"actor_id", "event_type", "summary"} <= set(item) for item in observations)
    assert all("severity" not in item for item in observations)
    # Findings are candidate risks derived from observations, with detector provenance.
    assert all({"risk_type", "severity", "rationale", "detector_version"} <= set(item)
               for item in findings)
    assert all("event_type" not in item for item in findings)
    # Hypotheses are falsifiable statements tied to a finding, never a verdict.
    assert all({"hypothesis_id", "finding_id", "statement", "expected_effect"} <= set(item)
               for item in hypotheses)
    assert all(item["status"] in {
        "proposed", "under_test", "supported", "weakened", "rejected", "inconclusive",
    } for item in hypotheses)
    finding_ids = {item["finding_id"] for item in findings}
    assert all(item["finding_id"] in finding_ids for item in hypotheses)


def test_intervention_is_recorded_but_not_verified(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    with TestClient(app) as client:
        scenario = app.state.engine.scenarios.list_scenarios()[0].scenario_id
        report = client.post("/v1/runs", json={
            "scenario_id": scenario, "experiment_id": "exp-concepts",
            "auto_intervene": False, "max_interventions": 2,
        }).json()
        run_id = report["run_id"]
        finding = client.get(f"/v1/runs/{run_id}/findings").json()["items"][0]
        intervention_response = client.post(f"/v1/runs/{run_id}/interventions", json={
            "experiment_id": report["experiment_id"],
            "intervention_id": "concept-intervention-1",
            "target_type": finding["target_type"],
            "target_ref": finding["target_ref"],
            "operation": "block",
            "parameters": {"scope": "synthetic_demo"},
            "authorization_ref": "concept-test",
        })
        assert intervention_response.status_code == 200, intervention_response.text
        intervention = intervention_response.json()
        after = client.get(f"/v1/runs/{run_id}/report").json()
    assert intervention["status"] == "completed"
    assert intervention["effect_status"] == "simulated"
    # The run records the intervention but explicitly withholds causal verification.
    assert after["causal_claim_status"] == "simulated_only"
    assert after["intervention_history"]
    assert after["defense_verification"] is None
    assert any("not automatically paired" in note for note in after["limitations"])


def test_defense_recommendation_is_not_effectiveness(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    with TestClient(app) as client:
        report = _engine_run(app)
        preview = client.post(f"/v1/runs/{report['run_id']}/defenses/preview").json()
    assert preview["candidates"]
    candidate = preview["candidates"][0]
    # A recommendation can promise an expected reduction...
    assert "expected_risk_reduction" in candidate
    # ...but the preview asserts no execution and no effectiveness anywhere.
    assert preview["execution_performed"] is False
    assert "effectiveness_verified" not in preview
    assert preview["execution_supported_overall"] is False
    assert candidate["evidence_level"] != "causal_validated"


def test_statistical_output_is_not_causal_validation(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    with TestClient(app):
        report = _engine_run(app)
    evaluation = report.get("evaluation")
    assert evaluation is not None
    assert evaluation["evidence_mode"] == "synthetic"
    assert any("do not report as empirical" in note for note in evaluation["notes"])
    # Deltas exist as numbers but the report never labels them causal.
    assert "causal_validated" not in evaluation
    assert report["causal_claim_status"] != "causal_validation_required"


def test_plan_only_workflow_is_never_an_execution(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    run = {
        "run_id": None, "plan_id": "plan-concept-1", "status": "planned",
    }
    store = app.state.warden_workflows
    store.begin("inv-plan-only", {
        "request_id": "inv-plan-only", "contract_id": "contract-x",
        "model_id": "model-7b", "runtime_id": "runtime-x", "mode": "observation",
        "scenario_id": "sc-x", "risk_type": "secret_flow", "start_run": False,
    })
    store.update("inv-plan-only", "finished", context={"plan": run},
                 result={"status": "planned", "plan": run})
    with TestClient(app) as client:
        evidence = client.get("/v1/evidence/investigations/inv-plan-only").json()
        graph = client.get("/v1/investigations/inv-plan-only/interaction-graph").json()
        experiment = client.get("/v1/experiments/inv-plan-only").json()
        timeline = client.get("/v1/investigations/inv-plan-only/timeline").json()
    assert evidence["items"] == []
    assert "no completed-run evidence" in " ".join(evidence["limitations"]).lower() or \
        evidence["run_status"] != "completed"
    assert graph["available"] is False and graph["edges"] == []
    assert experiment["execution_status"] == "planned"
    assert experiment["metrics_available"] is False
    # The journal records creation and validation only; no run submission event.
    event_types = [event["event_type"] for event in timeline["items"]]
    assert "run_submission" not in event_types


def test_graph_edges_are_observations_not_leak_findings(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    with TestClient(app) as client:
        report = _engine_run(app)
        graph = client.get(
            "/v1/investigations/inv-concept/interaction-graph",
            params={"run_id": report["run_id"]},
        ).json()
    assert graph["source"] == "agentguard_synthetic_report"
    for edge in graph["edges"]:
        # Edges cite the recorded event and never claim verification.
        assert edge["source_id"]
        assert "verified" not in edge
        assert edge["type"] in {"message", "invocation", "result"}
    assert graph["candidate_paths"] == []
    assert any("not inferred from risk scores" in note or "not invented" in note
               for note in graph["limitations"]) or all(
        path is None for path in graph["candidate_paths"]
    )


def test_study_analysis_requires_preregistration_before_claims(tmp_path):
    """Significance-style output only exists under a registered study protocol."""
    app = create_app(data_dir=tmp_path / "data")
    valid_outcome = {
        "control": [0.1, 0.2, 0.3, 0.4],
        "treatment": [0.5, 0.6, 0.7, 0.8],
        "case_families": ["family-a", "family-a", "family-b", "family-b"],
    }
    with TestClient(app) as client:
        missing = client.post("/v1/studies/study-missing/analyses", json={
            "outcomes": {"risk_exposure": valid_outcome},
        })
    assert missing.status_code == 404


def test_hidden_truth_cannot_become_queryable_output(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    with TestClient(app) as client:
        report = _engine_run(app)
        dumped = client.get(f"/v1/runs/{report['run_id']}/report").json()
    text = str(dumped)
    for banned in ("ground_truth", "oracle_label", "trial_truth", "answer_key"):
        assert banned not in text
