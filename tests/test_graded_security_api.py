"""分级API认证、只读预览与冻结动作提交边界。"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from agentguard.api.graded_security import register_graded_routes
from agentguard.artifacts.local_store import LocalArtifactStore


@pytest.fixture
def live_boundary(tmp_path):
    app = FastAPI()
    study = SimpleNamespace(arms=[SimpleNamespace(arm_id=m, method_id=m) for m in
        ["message_block", "message_replace", "leace", "activation_patch", "agent_isolation"]])
    app.state.research_registry = SimpleNamespace(get=lambda _: study)
    app.state.artifacts = LocalArtifactStore(tmp_path)
    submissions = []
    events = [{"event_id": "e1", "seq": 1, "type": "agent_started", "actor_id": "sender", "data": {}, "evidence_refs": ["a"]},
              {"event_id": "e2", "seq": 2, "type": "agent_started", "actor_id": "receiver", "data": {}, "evidence_refs": ["a"]},
              {"event_id": "e3", "seq": 3, "type": "message_delivered", "source_agent_id": "sender", "target_agent_id": "receiver", "message_id": "m", "data": {}, "evidence_refs": ["a"]}]

    def contract(_, arm):
        target = "agent:sender" if arm == "agent_isolation" else "activation:receiver:L12:resid_post:token=-1" if arm in {"leace", "activation_patch"} else "edge:sender->receiver"
        return {"status": "verified_frozen_contract", "contract": {"target_ref": target, "method_id": arm}}

    def submit(*args):
        submissions.append(args)
        return {"workflow_id": "new-workflow", "status": "planned"}

    service = SimpleNamespace(registry=app.state.research_registry,
        events=lambda *_: ({"run": {"run_id": "r"}}, events), execution_contract=contract, submit=submit)

    def authorize(token, _):
        if token != "op":
            raise HTTPException(403, "operator required")

    register_graded_routes(app, lambda: service, authorize, lambda *_a, **_k: None)
    return TestClient(app), submissions


def test_preview_references_authorization_and_unsupported_action(live_boundary):
    client, submissions = live_boundary
    base = "/v1/studies/s"
    profile = {"profile_id": "p", "study_id": "s", "workflow_id": "w", "policy_version": "v1", "source_ref": "approved-policy",
               "nodes": [{"agent_id": "sender", "protection_level": 4}, {"agent_id": "receiver", "protection_level": 5}],
               "channels": [{"edge_id": "sender->receiver", "assurance_level": 2}]}
    headers = {"X-AgentGuard-Operator-Token": "op"}
    assert client.post(base+"/security-profile", json=profile).status_code == 403
    saved = client.post(base+"/security-profile", json=profile, headers=headers)
    assert saved.status_code == 201
    pid = saved.json()["artifact_id"]
    event = {"event_id": "e3", "study_id": "s", "run_id": "r", "target_type": "edge", "target_ids": ["sender->receiver"],
             "harm_level": 4, "business_value": 5, "policy_violation": True, "evidence_refs": ["a"], "classification_source": "research-annotation"}
    saved = client.post(base+f"/value-events?workflow_id=w&profile_artifact_id={pid}", json=event, headers=headers)
    assert saved.status_code == 201
    payload = {"workflow_id": "w", "profile_artifact_id": pid, "event_artifact_id": saved.json()["artifact_id"]}
    readonly = client.post(base+"/intervention-decisions/preview", json=payload)
    assert readonly.status_code == 200
    assert not any(c["feasible"] for c in readonly.json()["payload"]["candidates"])
    response = client.post(base+"/intervention-decisions/preview", json=payload, headers=headers)
    decision = response.json()
    assert decision["payload"]["risk"]["risk_score"] == .74
    assert all(c["feasible"] for c in decision["payload"]["candidates"] if c["level"] == "L2")
    assert submissions == []
    did = decision["artifact_id"]
    assert client.get(base+f"/intervention-decisions/{did}").status_code == 200
    for method in ["component_ablation", "behavior_steering", "arbitrary_hook"]:
        assert client.post(base+f"/intervention-decisions/{did}/execute", json={"arm_id": method}, headers=headers).status_code == 422
    assert submissions == []
    assert client.post(base+f"/intervention-decisions/{did}/execute", json={"arm_id": "message_block"}).status_code == 403
    assert client.post(base+f"/intervention-decisions/{did}/execute", json={"arm_id": "message_block"}, headers=headers).status_code == 200
    assert len(submissions) == 1
