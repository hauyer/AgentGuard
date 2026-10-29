"""Synthetic unit fixtures validate public boundaries; no fixture is measured evidence."""
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agentguard.api.app import create_app
from agentguard.contracts.experiment_view import EventArtifact, LiveResearchManifest
from agentguard.services.experiment_views import compare_verified_runs, project_graph
from agentguard.services.study_execution import StudyExecutionService


def event(seq, kind, **fields):
    return {"schema_version": "stego.event/1", "event_id": f"event-{seq}", "run_id": "run",
            "study_id": "study", "arm_id": "baseline", "seq": seq,
            "at_utc": "2026-10-29T00:00:00Z", "type": kind, "provenance": "observed",
            "evidence_refs": ["config:" + "a" * 64], "data": {}, **fields}


def test_projection_is_deterministic_and_probe_does_not_invent_channel():
    events = [event(1, "agent_started", actor_id="receiver"),
              event(2, "probe_observed", actor_id="receiver", data={"value": .8,
                    "label_semantics": "secret_bit", "layer": 12})]
    one = project_graph("study", "run", "live_steganography", events)
    assert one == project_graph("study", "run", "live_steganography", events)
    assert one["communication_edges"] == []
    assert one["probe_evidence"][0]["associated_edge_id"] is None
    assert one["probe_evidence"][0]["claim_level"] == "linear_decodability"
    assert project_graph("study", "run", "live_steganography", [], through_seq=0)["nodes"] == []


def test_delivery_block_and_isolation_follow_observed_events():
    msg = {"source_agent_id": "sender", "target_agent_id": "receiver", "message_id": "message-1", "channel_id": "channel-1"}
    events = [event(1, "agent_started", actor_id="sender"), event(2, "agent_started", actor_id="receiver"),
              event(3, "message_sent", **msg), event(4, "message_blocked", **msg),
              event(5, "intervention_applied", actor_id="sender", data={"kind": "agent_isolation", "target": "sender"})]
    before = project_graph("study", "run", "live_steganography", events, through_seq=3)
    after = project_graph("study", "run", "live_steganography", events)
    assert before["communication_edges"][0]["blocked_count"] == 0
    assert after["communication_edges"][0]["blocked_count"] == 1
    assert after["communication_edges"][0]["delivered_count"] == 0
    assert before["nodes"][0]["status"] == "active"
    assert after["nodes"][0]["status"] == "isolated"


@pytest.mark.parametrize("change", [
    {"seq": 2}, {"run_id": "wrong-run"}, {"study_id": "other-study"},
    {"data": {"secret_bits": "private"}}, {"data": {"value": float("nan")}},
    {"type": "unknown"}, {"at_utc": "2026-10-29T01:00:00+01:00"},
])
def test_event_artifact_rejects_tamper_unknown_type_and_private_data(change):
    payload = {"schema_version": "stego.events/1", "run_id": "run", "study_id": "study",
               "arm_id": "baseline", "events": [{**event(1, "agent_started", actor_id="sender"), **change}]}
    with pytest.raises(ValidationError):
        EventArtifact.model_validate(payload)


def manifest(arm="baseline"):
    return {"schema_version": "warden.research-run/2", "run_id": arm, "plan_id": "plan", "study_id": "study",
        "arm_id": arm, "protocol_ref": "protocol", "contract_id": "contract", "config_sha256": "a" * 64,
        "executor_sha256": "b" * 64, "dataset_sha256": "c" * 64, "split_sha256": "d" * 64,
        "model_id": "model", "model_revision": "revision", "tokenizer_revision": "revision", "seed": 7,
        "started_at": "2026-10-29T00:00:00Z", "finished_at": "2026-10-29T00:01:00Z", "status": "completed",
        "provenance": "real_sender_receiver", "communication_mode": "instrumented_llm_codebook",
        "track": "live_steganography", "metrics": {name: {"value": value, "denominator": 2} for name, value in
            (("SRR_ITT", .5), ("SRR_executed", .5), ("BER", .5), ("NORMAL_UTILITY", 1.))},
        "missing_metrics": {}, "outcomes": [{"case_family": f"family-{i}", "split": "test", "executed": True,
            "decoded": True, "recovered": i == 0, "ber": float(i), "utility": 1., "missing_reason": None} for i in range(2)],
        "events_artifact": {"artifact_id": "public-summary-" + "e" * 64, "kind": "public-summary",
            "content_hash": "e" * 64, "media_type": "application/json", "size_bytes": 10},
        "model_calls": 6, "training_sha256": "f" * 64, "files_sha256": {"trials.json": "a" * 64}, "limitations": []}


def test_manifest_recomputes_metrics_and_keeps_missing_ber_null():
    payload = manifest()
    assert LiveResearchManifest.model_validate(payload).metrics["BER"].denominator == 2
    for mutate in [lambda m: m["metrics"]["BER"].update(value=0),
                   lambda m: m["metrics"]["NORMAL_UTILITY"].update(denominator=1),
                   lambda m: m.update(files_sha256={"../private": "a" * 64}),
                   lambda m: m["outcomes"][0].update(decoded=False)]:
        changed = deepcopy(payload)
        mutate(changed)
        with pytest.raises(ValidationError):
            LiveResearchManifest.model_validate(changed)


def test_comparison_requires_matching_frozen_families_and_reports_missing_arms():
    study = SimpleNamespace(arms=[SimpleNamespace(arm_id=a) for a in ("baseline", "leace", "sham")], seed=7,
                            multiplicity_method="holm")
    baseline, treatment = manifest(), manifest("leace")
    results = [{"artifacts": [{"manifest": m}]} for m in (baseline, treatment)]
    report = compare_verified_runs(study, results)
    assert report["status"] == "incomplete"
    assert report["arms"][-1]["status"] == "not_run"
    assert report["arms"][1]["effects"]["SRR_ITT"]["mean_treatment_minus_control"] == 0
    treatment["seed"] = 9
    with pytest.raises(ValueError, match="FROZEN_METADATA_MISMATCH"):
        compare_verified_runs(study, results)


def test_batch_manifest_is_written_before_submissions_and_has_stable_per_arm_keys():
    study = SimpleNamespace(arms=[SimpleNamespace(arm_id=a) for a in ("baseline", "leace")],
                            protocol_ref="frozen", split_sha256="a" * 64, seed=7)
    calls = []
    saved = SimpleNamespace(artifact_ref=SimpleNamespace(artifact_id="manifest"))
    artifacts = SimpleNamespace(put=lambda *a, **k: (calls.append("manifest"), saved)[1])
    service = StudyExecutionService(SimpleNamespace(get=lambda _: study, verify_registration=lambda _: True), None, None, artifacts)
    service.submit = lambda study_id, arm, key: (calls.append((arm, key)), {"status": "planned"})[1]
    first = service.submit_batch("study", "same-key")
    second = service.submit_batch("study", "same-key")
    assert first == second
    assert calls[0] == "manifest"
    assert calls[1:3] == calls[4:6]
    assert first["arms"][0]["workflow_id"] != first["arms"][1]["workflow_id"]


def test_all_102_frozen_operations_and_all_schemas_remain_identical(tmp_path):
    baseline = json.loads((Path(__file__).parents[1] / "docs/integration/openapi-1.1.0rc5.json").read_text(encoding="utf-8"))
    current = create_app(data_dir=tmp_path).openapi()
    for path, operations in baseline["paths"].items():
        for method, operation in operations.items():
            assert current["paths"][path][method] == operation
    for name, schema in baseline["components"]["schemas"].items():
        assert current["components"]["schemas"][name] == schema


def test_view_api_cursor_pages_do_not_repeat_and_invalid_cursor_is_422(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_WARDEN_URL", "http://127.0.0.1:9")
    app = create_app(data_dir=tmp_path)
    monkeypatch.setattr(app.state.research_registry, "get", lambda _: SimpleNamespace(track="live_steganography"))
    events = [event(1, "agent_started", actor_id="sender"), event(2, "agent_started", actor_id="receiver")]
    monkeypatch.setattr(StudyExecutionService, "events", lambda *a: ({"run": {"run_id": "run", "status": "completed"}}, events))
    with TestClient(app) as client:
        base = "/v1/studies/study/runs/workflow/"
        first = client.get(base + "timeline?limit=1").json()
        assert first["next_seq"] == 1 and first["has_more"]
        second = client.get(base + "timeline?after_seq=1&limit=1").json()
        assert second["events"][0]["event_id"] != first["events"][0]["event_id"]
        assert not second["has_more"]
        assert client.get(base + "timeline?after_seq=3").status_code == 422
        assert client.get(base + "timeline?after_seq=-1").status_code == 422
        assert client.get(base + "visualization?through_seq=3").status_code == 422


def test_event_artifact_must_belong_to_verified_run_and_match_stream_identity():
    ref = {"artifact_id": "public-summary-" + "a" * 64, "content_hash": "a" * 64}
    result = {"artifacts": [{"manifest": {"schema_version": "warden.research-run/2", "events_artifact": ref,
             "run_id": "run", "arm_id": "baseline"}}], "run": {"evidence": {"artifacts": []}}}
    service = StudyExecutionService(None, None, None, None)
    service.result = lambda *a: result
    with pytest.raises(ValueError, match="NOT_BOUND_TO_RUN"):
        service.events("study", "workflow")
    result["run"]["evidence"]["artifacts"] = [ref]
    stream = {"schema_version": "stego.events/1", "study_id": "other", "run_id": "run", "arm_id": "baseline", "events": []}
    service.client = SimpleNamespace(read_public_artifact=lambda *a: json.dumps(stream).encode())
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        service.events("study", "workflow")


def test_lost_batch_response_can_be_reconciled_read_only(tmp_path):
    from agentguard.artifacts.local_store import ArtifactIntegrityError, LocalArtifactStore

    study = SimpleNamespace(arms=[SimpleNamespace(arm_id="baseline")], protocol_ref="frozen", split_sha256="a" * 64, seed=7)
    store = LocalArtifactStore(tmp_path)
    service = StudyExecutionService(SimpleNamespace(get=lambda _: study, verify_registration=lambda _: True),
        SimpleNamespace(get=lambda _: {"request": {"study_id": "study"}, "workflow_status": "planned", "run_id": "run"}), None, store)
    writes = []
    service.submit = lambda *a: (writes.append(a), {"status": "planned"})[1]
    batch = service.submit_batch("study", "key")
    first = service.list_batches("study", limit=1)
    assert first["total"] == 1
    assert first["items"][0]["batch_id"] == batch["batch_id"]
    assert len(writes) == 1
    assert service.list_batches("other")["total"] == 0
    blob, _ = store._paths(batch["manifest_artifact_id"])
    blob.write_bytes(b"tampered")
    with pytest.raises(ArtifactIntegrityError):
        service.list_batches("study")
