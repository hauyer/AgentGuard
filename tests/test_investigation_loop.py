"""Contract tests for the evidence-driven multi-round investigation loop (D3)."""

import json

import pytest

from agentguard.adapters.warden import WardenKernelClient
from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.contracts.investigation_loop import (
    InvestigationLoopBudget,
    InvestigationLoopReport,
)
from agentguard.memory.store import ExperienceStore
from agentguard.services.evidence_gateway import EvidenceGateway
from agentguard.services.experiment_index import ExperimentIndexService
from agentguard.services.interaction_graph import InteractionGraphService
from agentguard.services.investigation_loop import (
    InvestigationLoopError,
    InvestigationLoopService,
)
from agentguard.services.observation_workflow import ObservationRunService
from agentguard.services.risk_findings import RiskFindingService
from agentguard.services.warden_workflow_store import WardenWorkflowStore


class FakeKernel:
    def __init__(self, *, contracts=None, ready=True):
        self.base_url = "https://warden.invalid"
        self._contracts = contracts or ["contract-loop"]
        self.ready = ready
        self.plan = {"plan_id": "plan-loop-1", "status": "planned", "blockers": []}
        self.run = {
            "run_id": "run-loop-1", "plan_id": "plan-loop-1", "status": "completed",
            "evidence": {
                "artifacts": [{"artifact_id": "obs-summary-" + "b" * 64}],
                "findings": [],
                "metrics": {"trace_alignment.duration_seconds": 3.0},
            },
        }

    def health(self):
        return {"status": "ok", "version": "0.2.0", "api_version": "v1"}

    def openapi_summary(self, *, force_refresh=False):
        return {
            "compatible": True, "openapi_version": "3.1.0", "service_version": "0.3.0",
            "path_count": 8, "schema_sha256": "a" * 64,
        }

    def capabilities(self):
        return [
            {"component_id": "agent.runtime", "available": self.ready,
             "capabilities": [],
             "metadata": {"runtime_id": "stegopot.v1",
                          "model_ids": ["model-7b"]} if self.ready else {}},
            {"component_id": "run.dispatcher", "available": True, "capabilities": []},
            {"component_id": "activation.backend", "available": False,
             "capabilities": []},
        ]

    def resources(self, kind=None):
        return []

    def contracts(self):
        return self._contracts

    def create_audit_plan(self, payload):
        return dict(self.plan)

    def start_run(self, plan_id):
        return dict(self.run)

    def get_run(self, run_id):
        return dict(self.run)


class FakeClock:
    """Advances by a fixed step on every read so time budgets can elapse."""

    def __init__(self, step=0.0):
        self.now = 1000.0
        self.step = step

    def __call__(self):
        self.now += self.step
        return self.now


def _seed_workflows(workflows, request_id="inv-loop"):
    run = {
        "run_id": f"run-{request_id}", "plan_id": f"plan-{request_id}",
        "status": "completed",
        "evidence": {
            "findings": [{"kind": "observational_run", "messages": [
                {"message_id": "msg-1", "sender": "rev_a", "recipient": "chair", "round_index": 0},
                {"message_id": "msg-2", "sender": "rev_b", "recipient": "chair", "round_index": 0},
                {"message_id": "msg-3", "sender": "rev_c", "recipient": "chair", "round_index": 0},
            ]}],
            "artifacts": [{"artifact_id": "art-" + "a" * 60}],
        },
    }
    workflows.begin(request_id, {
        "request_id": request_id, "contract_id": "contract-loop", "model_id": "model-7b",
        "runtime_id": "stegopot.v1", "mode": "observation", "scenario_id": "sc-loop",
        "metadata": {"source_kind": "synthetic"},
    })
    workflows.update(request_id, "finished", context={"run": run},
                     result={"status": "completed", "run": run})
    return run


def _service(tmp_path, kernel=None, llm_client=None, clock=None):
    workflows = WardenWorkflowStore(tmp_path / "workflows.sqlite3")
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    observation = None
    if kernel is not None:
        observation = ObservationRunService(
            client=kernel, workflows=workflows, artifacts=artifacts,
            experiences=ExperienceStore(tmp_path / "exp.sqlite3"),
        )
    return InvestigationLoopService(
        workflows=workflows,
        artifacts=artifacts,
        evidence_gateway=EvidenceGateway(),
        experiment_index=ExperimentIndexService(workflows, artifacts),
        risk_findings=RiskFindingService(InteractionGraphService()),
        experiences=ExperienceStore(tmp_path / "exp.sqlite3"),
        observation=observation,
        llm_client=llm_client,
        monotonic=clock or FakeClock(),
    )


def test_multi_round_read_only_loop_records_adaptive_decisions(tmp_path):
    service = _service(tmp_path)
    _seed_workflows(service.workflows)
    report = service.run(
        "inv-loop", strategy="information_value",
        budget=InvestigationLoopBudget(max_actions=6, no_new_evidence_rounds=3),
    )
    assert isinstance(report, InvestigationLoopReport)
    assert len(report.decisions) >= 2
    types = [d.selected_action for d in report.decisions]
    assert len(set(types)) >= 2, "rounds must adapt instead of repeating one action"
    for decision in report.decisions:
        assert decision.selection_strategy == "information_value"
        assert decision.selection_reason
        assert decision.candidate_actions
        assert decision.actual_cost >= 0
    assert report.metrics.steps_to_first_evidence is not None
    assert report.metrics.effective_action_ratio == 1.0
    assert report.report_artifact_id
    assert any("source_kind=synthetic" in item for item in report.limitations)
    # Intervention planning, when it runs, stays plan-only.
    intervention_rounds = [
        d for d in report.decisions if d.selected_action == "act-plan-intervention"
    ]
    if intervention_rounds:
        assert any("no executor is declared" in note for note in report.limitations)
        assert all(d.result_status == "completed" for d in intervention_rounds)


def test_budget_and_manual_stop_are_respected(tmp_path):
    service = _service(tmp_path)
    _seed_workflows(service.workflows)
    limited = service.run(
        "inv-loop", budget=InvestigationLoopBudget(max_actions=2),
    )
    assert len(limited.decisions) == 2
    assert limited.stop_reason == "action_budget_reached"

    manual = service.run(
        "inv-loop", budget=InvestigationLoopBudget(max_actions=9),
        stop_after_rounds=1,
    )
    assert len(manual.decisions) == 1
    assert manual.stop_reason == "manual_stop"


def test_time_budget_stops_the_loop(tmp_path):
    clock = FakeClock(step=6.0)  # every monotonic read advances past half the budget
    service = _service(tmp_path, clock=clock)
    _seed_workflows(service.workflows)
    budget = InvestigationLoopBudget(max_actions=9, max_seconds=10.0)
    report = service.run("inv-loop", budget=budget)
    assert report.stop_reason == "time_budget_reached"
    assert len(report.decisions) < 9


def test_audit_is_not_submitted_when_less_than_one_second_remains(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import agentguard.services.investigation_loop as loop_module

    service = _service(tmp_path, kernel=FakeKernel())
    _seed_workflows(service.workflows)
    clock_values = iter([0.0, 0.0, 9.2, 9.3, 10.0])
    service._monotonic = lambda: next(clock_values)
    monkeypatch.setattr(
        loop_module,
        "select_next_action",
        lambda *args, **kwargs: SimpleNamespace(
            action_id="act-submit-audit", degraded=False, degradation_note="",
            reason="selected audit",
        ),
    )
    monkeypatch.setattr(
        service, "_execute_action",
        lambda *args, **kwargs: pytest.fail("audit action exceeded its remaining budget"),
    )

    report = service.run(
        "inv-loop", strategy="risk_score",
        budget=InvestigationLoopBudget(max_actions=4, max_seconds=10.0),
    )

    assert report.stop_reason == "time_budget_reached"
    assert report.decisions == []


def test_observation_audit_timeout_uses_remaining_loop_budget(tmp_path):
    from types import SimpleNamespace

    class RecordingObservation:
        def execute(self, config, *, request_id):
            self.config = config
            self.request_id = request_id
            return SimpleNamespace(
                execution_status="blocked", blockers=["live gate"],
                run_id=None, evidence_refs=[],
            )

    service = _service(tmp_path)
    observation = RecordingObservation()
    service.observation = observation
    result = service._submit_audit(
        {"request": {"contract_id": "contract-loop", "model_id": "model-7b",
                     "runtime_id": "stegopot.v1"}},
        "inv-loop", 1, {"audit_ids": []}, InvestigationLoopBudget(max_seconds=300.0),
        remaining_seconds=7.25,
    )

    assert observation.config.time_budget == 7.25
    assert observation.request_id == "inv-loop-audit-r1"
    assert result[0] == "blocked"


def test_http_stub_observation_feedback_informs_the_next_decision(
    tmp_path, monkeypatch, warden_openapi_document,
):
    """The HTTP stub is contract evidence only, not a live WARDEN/model run."""

    class WardenHTTPStub:
        def __init__(self, openapi_document):
            self.calls = []
            self.openapi_document = openapi_document

        def __call__(self, method, url, headers, body, timeout):
            path = url.removeprefix("https://warden.stub")
            payload = json.loads(body) if body else None
            self.calls.append((method, path, payload))
            if path == "/openapi.json":
                response = self.openapi_document
            elif path == "/v1/health":
                response = {"status": "ok", "version": "stub", "api_version": "v1"}
            elif path == "/v1/contracts":
                response = ["contract-loop"]
            elif path == "/v1/capabilities":
                response = [
                    {"component_id": "agent.runtime", "available": True,
                     "capabilities": [],
                     "metadata": {"runtime_id": "stegopot.v1", "model_ids": ["model-7b"]}},
                    {"component_id": "run.dispatcher", "available": True,
                     "capabilities": []},
                    {"component_id": "activation.backend", "available": False,
                     "capabilities": []},
                ]
            elif method == "POST" and path == "/v1/audit-plans":
                response = {"plan_id": "plan-http-stub", "status": "planned", "blockers": []}
            elif method == "POST" and path == "/v1/runs":
                response = {
                    "run_id": "run-http-stub", "plan_id": "plan-http-stub",
                    "status": "running",
                }
            elif method == "GET" and path == "/v1/runs/run-http-stub":
                response = {
                    "run_id": "run-http-stub", "plan_id": "plan-http-stub",
                    "status": "completed",
                    "evidence_refs": ["artifact://stub/observation-001"],
                }
            else:
                raise AssertionError(f"Unexpected WARDEN API request: {method} {path}")
            return 200, json.dumps(response).encode("utf-8")

    transport = WardenHTTPStub(warden_openapi_document)
    kernel = WardenKernelClient("https://warden.stub", transport=transport)
    monkeypatch.setattr("agentguard.services.warden_lifecycle.time.sleep", lambda _: None)
    service = _service(tmp_path, kernel=kernel)
    _seed_workflows(service.workflows)
    report = service.run(
        "inv-loop", strategy="risk_score",
        budget=InvestigationLoopBudget(max_actions=9, max_seconds=300),
    )
    assert "act-submit-audit" in [d.selected_action for d in report.decisions]
    audit_round = next(
        d for d in report.decisions if d.selected_action == "act-submit-audit"
    )
    assert audit_round.result_status == "completed"
    assert report.created_audit_request_ids
    audit_entry = service.workflows.get(report.created_audit_request_ids[0])
    assert audit_entry is not None and audit_entry["status"] == "finished"
    audit_index = report.decisions.index(audit_round)
    next_decision = report.decisions[audit_index + 1]
    assert next_decision.selected_action == "act-read-evidence"
    assert "artifact://stub/observation-001" in next_decision.evidence_refs
    assert [path for _, path, _ in transport.calls].count("/v1/audit-plans") == 1
    assert [path for _, path, _ in transport.calls].count("/v1/runs") == 1
    assert [path for _, path, _ in transport.calls].count("/v1/runs/run-http-stub") == 1
    submitted = next(payload for method, path, payload in transport.calls
                     if method == "POST" and path == "/v1/audit-plans")
    assert submitted["request_id"] == report.created_audit_request_ids[0]
    assert submitted["mode"] == "capture_only"
    assert "scenario_id" not in submitted
    assert "authorization_ref" not in submitted
    submitted = next(payload for method, path, payload in transport.calls
                     if method == "POST" and path == "/v1/audit-plans")
    assert submitted["request_id"] == report.created_audit_request_ids[0]


def test_completed_run_without_evidence_refs_blocks_loop_completion(tmp_path):
    kernel = FakeKernel()
    kernel.run = {
        "run_id": "run-loop-without-evidence",
        "plan_id": "plan-loop-1",
        "status": "completed",
        "evidence": {"artifacts": [], "findings": []},
    }
    service = _service(tmp_path, kernel=kernel)
    _seed_workflows(service.workflows)

    report = service.run(
        "inv-loop", strategy="risk_score",
        budget=InvestigationLoopBudget(max_actions=9, max_seconds=300),
    )

    audit_action = next(
        item for item in report.decisions if item.selected_action == "act-submit-audit"
    )
    assert audit_action.result_status == "blocked"
    assert audit_action.evidence_refs == []
    assert report.phase == "BLOCKED"
    assert report.stop_reason == "capability_unavailable"
    assert any("WARDEN_RESULT_EVIDENCE_MISSING" in item for item in report.limitations)


def test_blocked_kernel_stops_with_capability_unavailable(tmp_path):
    kernel = FakeKernel(ready=False)
    service = _service(tmp_path, kernel=kernel)
    _seed_workflows(service.workflows)
    report = service.run(
        "inv-loop", strategy="risk_score",
        budget=InvestigationLoopBudget(max_actions=9, max_seconds=300),
    )
    assert report.phase == "BLOCKED"
    assert report.stop_reason == "capability_unavailable"
    audit_round = next(
        (d for d in report.decisions if d.selected_action == "act-submit-audit"),
        None,
    )
    assert audit_round is not None and audit_round.result_status == "blocked"


def test_no_new_evidence_stops_repeat_rounds(tmp_path):
    service = _service(tmp_path)
    _seed_workflows(service.workflows)
    report = service.run(
        "inv-loop", strategy="fixed_order",
        budget=InvestigationLoopBudget(max_actions=20, no_new_evidence_rounds=2),
    )
    assert report.stop_reason in {"no_new_evidence", "action_budget_reached"}
    if report.stop_reason == "no_new_evidence":
        assert len(report.decisions) < 20
        assert report.metrics.redundant_action_count >= 1


def test_unknown_base_investigation_is_rejected(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(InvestigationLoopError):
        service.run("inv-missing")


def test_llm_strategy_degrades_deterministically_without_client(tmp_path):
    service = _service(tmp_path)
    _seed_workflows(service.workflows)
    report = service.run(
        "inv-loop", strategy="llm",
        budget=InvestigationLoopBudget(max_actions=3, max_model_calls=2),
    )
    assert report.strategy_degraded is True
    assert "fallback" in report.strategy_degradation_note
    assert report.metrics.model_calls_used == 0
    assert len(report.decisions) == 3


def test_llm_strategy_uses_valid_selections_and_counts_model_calls(tmp_path):
    class FakeLLM:
        def __init__(self):
            self.calls = 0
            self.timeouts = []

        def complete_json(self, *, system_prompt, payload, max_tokens, timeout_seconds=None):
            self.calls += 1
            self.timeouts.append(timeout_seconds)
            candidates = payload["candidate_actions"]
            target = next(
                (item for item in candidates
                 if not item["taken"] and item["action_type"] == "read_evidence"),
                candidates[0],
            )
            return {
                "selected_action_id": target["action_id"],
                "rationale": "evidence first per protocol",
            }

    llm = FakeLLM()
    service = _service(tmp_path, llm_client=llm)
    _seed_workflows(service.workflows)
    report = service.run(
        "inv-loop", strategy="llm",
        budget=InvestigationLoopBudget(max_actions=2, max_model_calls=2),
    )
    assert report.strategy_degraded is False
    assert report.metrics.model_calls_used == 2
    assert all("llm selection" in d.selection_reason for d in report.decisions)
    assert len(llm.timeouts) == 2
    assert all(0.0 < timeout <= 300.0 for timeout in llm.timeouts)


def test_llm_strategy_rejects_out_of_set_ids(tmp_path):
    class FakeLLM:
        def complete_json(self, *, system_prompt, payload, max_tokens):
            return {"selected_action_id": "act-not-in-set", "rationale": "hallucinated"}

    service = _service(tmp_path, llm_client=FakeLLM())
    _seed_workflows(service.workflows)
    report = service.run("inv-loop", strategy="llm",
                         budget=InvestigationLoopBudget(max_actions=1))
    assert report.strategy_degraded is True
    assert "outside the candidate set" in report.strategy_degradation_note
    assert report.decisions[0].selected_action != "act-not-in-set"
    assert report.metrics.model_calls_used == 1


def test_experience_strategy_degrades_without_records(tmp_path):
    service = _service(tmp_path)
    _seed_workflows(service.workflows)
    report = service.run("inv-loop", strategy="experience",
                         budget=InvestigationLoopBudget(max_actions=2))
    assert report.strategy_degraded is True
    assert "no applicable verified records" in report.strategy_degradation_note


def test_loop_report_artifact_holds_no_sensitive_payloads(tmp_path):
    service = _service(tmp_path)
    _seed_workflows(service.workflows)
    report = service.run("inv-loop", budget=InvestigationLoopBudget(max_actions=3))
    payload = service.artifacts.get_bytes(report.report_artifact_id).decode("utf-8")
    for banned in ("ground_truth", "sender", "recipient", "content"):
        assert banned not in payload
