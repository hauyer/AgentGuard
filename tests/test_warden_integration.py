import json

import pytest
from fastapi.testclient import TestClient

from agentguard.adapters.warden import WardenKernelClient, WardenKernelError, warden_status
from agentguard.api.app import create_app
from agentguard.contracts.warden import WardenAuditRequest


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body, timeout))
        status, payload = self.responses.pop(0)
        return status, json.dumps(payload).encode("utf-8")


def _payload(**overrides):
    value = {
        "request_id": "req-1",
        "contract_id": "contract-1",
        "model_id": "model-1",
        "runtime_id": "runtime-1",
        "mode": "causal_audit",
        "capture_targets": [{
            "target_id": "receiver-resid-12",
            "agent_ids": ["receiver"],
            "layer": 12,
            "component": "resid_post",
            "token_policy": "last_token",
            "retain_gradient": True,
        }],
        "metadata": {},
    }
    value.update(overrides)
    return value


def test_warden_request_matches_kernel_contract_and_excludes_local_control_field():
    request = WardenAuditRequest.model_validate(_payload(start_run=False))
    payload = request.to_warden_payload()
    assert "start_run" not in payload
    assert "authorization_ref" not in payload
    assert payload["mode"] == "causal_audit"
    assert payload["capture_targets"][0]["agent_ids"] == ["receiver"]


def test_observation_request_requires_no_capture_target():
    request = WardenAuditRequest.model_validate(
        _payload(mode="observation", capture_targets=[])
    )
    payload = request.to_warden_payload()
    assert payload["mode"] == "observation"
    assert payload["capture_targets"] == []


def test_warden_client_does_not_start_blocked_plan():
    transport = FakeTransport([
        (200, {"plan_id": "plan-1", "status": "blocked", "blockers": ["Probe missing"]}),
    ])
    client = WardenKernelClient("https://warden.local", transport=transport)
    result = client.submit_audit(_payload(), start_run=True)
    assert result["submission_status"] == "blocked"
    assert result["run"] is None
    assert len(transport.calls) == 1
    assert transport.calls[0][1].endswith("/v1/audit-plans")


def test_warden_client_submits_only_after_nonblocked_plan():
    transport = FakeTransport([
        (200, {"plan_id": "plan-1", "status": "planned", "blockers": []}),
        (200, {"run_id": "run-1", "plan_id": "plan-1", "status": "blocked", "blockers": ["No dispatcher"]}),
    ])
    client = WardenKernelClient("https://warden.local", transport=transport)
    result = client.submit_audit(_payload(), start_run=True)
    assert result["run"]["run_id"] == "run-1"
    assert result["submission_status"] == "blocked"
    assert [call[0] for call in transport.calls] == ["POST", "POST"]
    assert json.loads(transport.calls[1][3]) == {"plan_id": "plan-1"}


def test_warden_client_removes_hidden_truth_from_old_kernel_plan_responses():
    plan = {"plan_id": "plan-1", "status": "blocked", "threat_contract": {
        "contract_id": "contract-1", "scenario_id": "scenario-1",
        "harmful_objective": "hidden-sentinel target",
        "truth": {"secret": "hidden-sentinel"},
    }}
    transport = FakeTransport([(200, plan), (200, plan)])
    client = WardenKernelClient("https://warden.local", transport=transport)
    created = client.create_audit_plan(_payload())
    fetched = client.get_audit_plan("plan-1")
    assert "truth" not in created["threat_contract"]
    assert "truth" not in fetched["threat_contract"]
    assert set(created["threat_contract"]) == {"contract_id", "scenario_id"}
    assert "hidden-sentinel" not in str((created, fetched))


def test_warden_client_fails_closed_on_unknown_plan_status():
    transport = FakeTransport([(200, {"plan_id": "plan-unknown", "status": "new-status"})])
    client = WardenKernelClient("https://warden.local", transport=transport)
    result = client.submit_audit(_payload(), start_run=True)
    assert result["submission_status"] == "new-status"
    assert result["run"] is None
    assert len(transport.calls) == 1


def test_warden_client_rejects_non_loopback_plain_http():
    with pytest.raises(ValueError, match="HTTPS"):
        WardenKernelClient("http://warden.example")


def test_warden_client_sanitizes_http_errors():
    transport = FakeTransport([(503, {"detail": {"error": "kernel offline", "secret": "must-not-leak"}})])
    client = WardenKernelClient("https://warden.local", transport=transport)
    with pytest.raises(WardenKernelError) as caught:
        client.health()
    assert caught.value.status_code == 503
    assert "must-not-leak" not in str(caught.value)


def test_warden_status_distinguishes_not_configured(monkeypatch):
    monkeypatch.delenv("AGENTGUARD_WARDEN_URL", raising=False)
    assert warden_status()["status"] == "not_configured"


def test_kernel_status_endpoint_does_not_claim_local_kernel(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTGUARD_WARDEN_URL", raising=False)
    client = TestClient(create_app(data_dir=tmp_path))
    response = client.get("/v1/kernel/status")
    assert response.status_code == 200
    assert response.json()["status"] == "not_configured"
    assert response.json()["kernel"] == "WARDEN"


def test_kernel_audit_endpoint_returns_503_when_kernel_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTGUARD_WARDEN_URL", raising=False)
    client = TestClient(create_app(data_dir=tmp_path))
    response = client.post("/v1/kernel/audits", json=_payload())
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "WARDEN_NOT_CONFIGURED"


def test_defense_evaluation_requires_agentguard_approval(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTGUARD_WARDEN_URL", raising=False)
    monkeypatch.setenv("AGENTGUARD_APPROVAL_TOKEN", "trusted-approval")
    client = TestClient(create_app(data_dir=tmp_path))
    response = client.post(
        "/v1/kernel/audits",
        json=_payload(mode="defense_evaluation", authorization_ref="invalid"),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "OPERATOR_APPROVAL_REQUIRED"


def test_kernel_audit_endpoint_forwards_request_and_preserves_blocked_status(tmp_path, monkeypatch):
    transport = FakeTransport([
        (200, {"plan_id": "plan-9", "status": "blocked", "blockers": ["Probe not found"]}),
    ])
    kernel = WardenKernelClient("https://warden.local", transport=transport)
    monkeypatch.setattr(WardenKernelClient, "from_env", classmethod(lambda cls: kernel))
    client = TestClient(create_app(data_dir=tmp_path))
    response = client.post("/v1/kernel/audits", json=_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["kernel"] == "WARDEN"
    assert body["status"] == "blocked"
    assert body["run"] is None
    assert body["causal_claim_status"] == "not_assessed"
    sent = json.loads(transport.calls[0][3])
    assert "start_run" not in sent
    assert sent["request_id"] == "req-1"
