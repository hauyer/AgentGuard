"""Batch membership, validity gate and threshold semantics; fixtures are not real trials."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_experiment_views import event, manifest

from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.services.experiment_views import compare_verified_runs, project_graph
from agentguard.services.study_execution import StudyExecutionService


def study():
    return SimpleNamespace(arms=[SimpleNamespace(arm_id=a) for a in ("baseline","leace")],
                           seed=7, multiplicity_method="holm", protocol_ref="frozen",
                           split_sha256="a"*64, dataset_version="b"*64, model_revision="revision")


def test_unscoped_history_cannot_pick_earliest_or_best_run():
    rows = [manifest(), manifest("leace"), {**manifest(),"run_id":"second-baseline"}]
    results = [{"artifacts":[{"manifest":m}]} for m in rows]
    for ordered in (results,list(reversed(results))):
        report = compare_verified_runs(study(),ordered)
        assert report["status"] == "incomplete"
        assert report["blockers"] == ["BATCH_SCOPE_REQUIRED"]


def test_exact_submission_key_reconciliation_is_read_only_and_study_scoped():
    import hashlib
    wf="study-"+hashlib.sha256(b"study:intent-1").hexdigest()
    entry={"run_id":"run-known","workflow_status":"running",
           "request":{"study_id":"study","study_arm_id":"leace"}}
    service=StudyExecutionService(SimpleNamespace(get=lambda _:study()),
        SimpleNamespace(get=lambda key:entry if key==wf else None),None,None)
    expected=service.lookup_intent("study","intent-1")
    assert expected["run_id"]=="run-known" and expected["arm_id"]=="leace"
    assert service.lookup_intent("study","different")["registered"] is False
    assert service.lookup_intent("other","intent-1")["registered"] is False


@pytest.mark.parametrize("partial,failed",[(False,False),(True,False),(False,True)])
def test_two_batches_remain_separate_despite_interleaved_completion(tmp_path,partial,failed):
    registered = study()
    entries, results = {}, {}
    service = StudyExecutionService(SimpleNamespace(get=lambda _:registered,
        verify_registration=lambda _:True),SimpleNamespace(get=entries.get),None,
        LocalArtifactStore(tmp_path))
    def submit(study_id, arm, key):
        import hashlib
        wf="study-"+hashlib.sha256(f"{study_id}:{key}".encode()).hexdigest()
        run=key+":run"
        entries[wf]={"request":{"study_id":study_id},"run_id":run,"workflow_status":"completed"}
        m=manifest(arm);m.update(run_id=run)
        results[wf]={"artifacts":[{"manifest":m}]}
        return {"status":"completed"}
    service.submit=submit
    one=service.submit_batch("study","one")
    two=service.submit_batch("study","two")
    # Completion and reconciliation order cannot affect the frozen membership.
    results=dict(reversed(list(results.items())))
    service.result=lambda _,wf:results[wf]
    if partial:
        results[one["arms"][1]["workflow_id"]]={"artifacts":[]}
    if failed:
        results[one["arms"][1]["workflow_id"]]["artifacts"][0]["manifest"]["status"]="failed"
    first=service.compare_batch("study",one["batch_id"])
    second=service.compare_batch("study",two["batch_id"])
    assert first==service.compare_batch("study",one["batch_id"])
    assert first["status"] == ("incomplete" if partial or failed else "observed")
    assert second["status"] == "observed"
    assert {r["run_id"] for r in first["selected_runs"]}.isdisjoint(
        r["run_id"] for r in second["selected_runs"])
    # Adding a successful retry outside the immutable batch never replaces its failure.
    entries["retry"]={"request":{"study_id":"study"},"run_id":"new","workflow_status":"completed"}
    assert first==service.compare_batch("study",one["batch_id"])


def test_v2_replicate_has_unique_submission_intent_and_read_only_history(tmp_path):
    registered=study();store=LocalArtifactStore(tmp_path)
    service=StudyExecutionService(SimpleNamespace(get=lambda _:registered,
        verify_registration=lambda _:True),SimpleNamespace(get=lambda _:None),None,store)
    calls=[]
    service.submit=lambda *a,**k:calls.append((a,k)) or {"status":"queued"}
    first=service.submit_batch("study","key",replicate_id="r1")
    assert first["replicate_id"] == "r1"
    assert calls[0][1]["batch_id"] == first["batch_id"]
    before=len(calls)
    assert service.list_batches("study")["items"][0]["replicate_id"]=="r1"
    assert len(calls)==before
    with pytest.raises(ValueError,match="REPLICATE_CONFLICT"):
        service.submit_batch("study","key",replicate_id="r2")
    with pytest.raises(ValueError,match="ALREADY_REGISTERED"):
        service.submit_batch("study","different-key",replicate_id="r1")
    second=service.submit_batch("study","another-key",replicate_id="r2")
    assert first["batch_id"] != second["batch_id"]


def test_different_seed_model_or_data_cannot_be_paired():
    for field in ("seed","model_revision","dataset_sha256","split_sha256"):
        base,treatment=manifest(),manifest("leace")
        treatment[field]="different"
        with pytest.raises(ValueError,match="FROZEN_METADATA_MISMATCH"):
            compare_verified_runs(study(),[{"artifacts":[{"manifest":m}]} for m in (base,treatment)])


def test_probe_below_threshold_does_not_become_positive_or_create_edge():
    events=[event(1,"agent_started",actor_id="receiver"),
            event(2,"probe_observed",actor_id="receiver",data={"value":.2,"threshold":.8,
                "label_semantics":"secret_bit","probe_id":"probe/2"})]
    graph=project_graph("study","run","live_steganography",events)
    assert graph["communication_edges"]==[]
    assert graph["probe_evidence"][0]["threshold_passed"] is False
    assert graph["probe_evidence"][0]["detection_status"]=="below_threshold"
    assert graph["probe_evidence"][0]["probe_model_version"]=="probe/2"


def test_no_literal_match_is_not_applied_replacement():
    graph=project_graph("study","run","live_steganography",[
        event(1,"intervention_applied",data={"kind":"message_replace","target":"sender->receiver",
                                         "mode":"literal_match_no_change"})])
    assert graph["interventions"][0]["execution_state"]=="not_run"


def test_contextual_manifest_requires_actual_disjoint_model_inputs():
    from pydantic import ValidationError

    from agentguard.contracts.stego_remediation import LiveResearchManifestV3
    m=manifest()
    m.update(schema_version="warden.research-run/3",protocol_version="controlled-collaborative/3",
        outcome_schema_version="stego.outcome/2",batch_id="batch",replicate_id="r1",method_version="3",
        scenario="two_agent_v2",baseline_validity_gate={"minimum":.8},cost={"monetary_cost":None},
        training_inputs={"dataset_sha256":m["dataset_sha256"],"split_sha256":m["split_sha256"],"model_files_sha256":{}},
        training_input_hashes={"train":["1"*64],"validation":["2"*64]})
    for i,o in enumerate(m["outcomes"]):
        o.update(outcome_schema_version="stego.outcome/2",receiver_executed=True,carrier_present=True,
            carrier_valid=True,bit_emitted=True,bit_valid=True,secret_recovered=o["recovered"],
            decode_missing_reason=None,carrier_missing_reason=None,utility_independent=1.,
            utility_collaborative=1.,task_missing_reason=None,execution_success=True,
            receiver_input_sha256=str(i+3)*64)
    for name,value,n in [("UTILITY_INDEPENDENT",1.,2),("UTILITY_COLLABORATIVE",1.,2),
        ("EXECUTION_SUCCESS_RATE",1.,2),("CARRIER_DELIVERY_RATE",1.,2),("CARRIER_VALID_RATE",1.,2),
        ("SRR_EXECUTED",.5,2),("SRR_NO_CARRIER",None,0),("SRR_VALID_CARRIER",.5,2),
        ("INVERTED_READOUT_SRR_ITT",.5,2),("RECEIVER_INPUT_NOVELTY_RATE",1.,2)]:m["metrics"][name]={"value":value,"denominator":n}
    LiveResearchManifestV3.model_validate(m)
    changed=deepcopy(m);changed["outcomes"][0]["receiver_input_sha256"]="1"*64
    with pytest.raises(ValidationError,match="overlap"):LiveResearchManifestV3.model_validate(changed)

    changed=deepcopy(m);changed["training_input_hashes"]["validation"]=["1"*64]
    with pytest.raises(ValidationError,match="overlap"):LiveResearchManifestV3.model_validate(changed)


def test_preview_target_cannot_override_warden_frozen_target():
    arm=SimpleNamespace(arm_id="leace",method_id="leace",method_version="3",
        target_ref="activation:receiver:L12:resid_post:token=-1",parameter_sha256="e"*64)
    s=SimpleNamespace(arms=[arm],protocol_ref="p",model_id="m",model_revision="rev",
        tokenizer_revision="rev",dataset_version="a"*64,split_sha256="b"*64,seed=7)
    actual={"model_id":"m","model_revision":"rev","tokenizer_revision":"rev",
        "dataset_sha256":"a"*64,"split_sha256":"b"*64,"seed":7,"method_id":"leace",
        "method_version":"3","target_ref":arm.target_ref,"config_sha256":"e"*64,"available":True}
    calls=[]
    service=StudyExecutionService(SimpleNamespace(get=lambda _:s),None,
        SimpleNamespace(execution_contract=lambda *args:calls.append(args) or actual),None)
    assert service.execution_contract("study","leace")["status"]=="verified_frozen_contract"
    assert calls==[("p","leace")]
    arm.target_ref="agent:sender"
    with pytest.raises(ValueError,match="TARGET_OR_PARAMETERS_MISMATCH"):
        service.execution_contract("study","leace")
