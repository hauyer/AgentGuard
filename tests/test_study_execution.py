"""Study execution failures remain explicit; these are unit tests with doubles."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agentguard.contracts.research_result import ResearchMetricValue, WardenResearchManifest
from agentguard.services.study_execution import StudyExecutionService


def test_missing_execution_metadata_blocks_without_contacting_warden():
    study = SimpleNamespace(arms=[SimpleNamespace(arm_id="baseline")], protocol_ref=None,
                            warden_contract_id=None, warden_runtime_id=None, warden_mode=None,
                            model_id=None, warden_capture_targets=[])
    registry = SimpleNamespace(get=lambda _: study, verify_registration=lambda _: True)
    service = StudyExecutionService(registry, None, None, None)
    assert service.submit("study", "baseline", "key") == {
        "status": "blocked", "blockers": ["STUDY_EXECUTION_METADATA_MISSING"],
    }
    with pytest.raises(ValueError, match="STUDY_ARM_NOT_REGISTERED"):
        service.submit("study", "unregistered", "key")


def test_other_study_cannot_read_known_workflow():
    workflows = SimpleNamespace(get=lambda _: {"request": {"study_id": "owner"}})
    service = StudyExecutionService(None, workflows, None, None)
    with pytest.raises(KeyError):
        service.result("another-study", "known-workflow")


def test_corrupt_preregistration_blocks_before_submission():
    registry = SimpleNamespace(get=lambda _: object(), verify_registration=lambda _: False)
    service = StudyExecutionService(registry, None, None, None)
    with pytest.raises(ValueError, match="STUDY_REGISTRATION_CORRUPT"):
        service.submit("study", "baseline", "key")


def test_manifest_schema_rejects_nonfinite_metrics_zero_denominators_and_wrong_times():
    with pytest.raises(ValidationError):
        ResearchMetricValue(value=float("nan"), denominator=24)
    with pytest.raises(ValidationError):
        ResearchMetricValue(value=24, denominator=0)
    manifest = {
        "schema_version": "warden.research-run/1", "run_id": "run", "plan_id": "plan",
        "study_id": "study", "arm_id": "baseline", "protocol_ref": "protocol",
        "contract_id": "contract", "config_sha256": "a" * 64, "executor_sha256": "b" * 64,
        "dataset_sha256": "c" * 64, "split_sha256": "d" * 64, "model_id": "model",
        "model_revision": "revision", "tokenizer_revision": "revision", "seed": 19,
        "started_at": "2026-10-29T03:00:00Z", "finished_at": "2026-10-29T03:01:00Z",
        "status": "completed", "provenance": "local_real_model", "track": "mechanism_testbed",
        "files_sha256": {"manifest.json": "e" * 64}, "model_files_sha256": {"weights": "f" * 64},
        "metrics": {"capture_count": {"value": 24, "denominator": 24}},
        "missing_metrics": {"BER": "Live Receiver not executed"}, "dependencies": {"torch": "2"},
    }
    assert WardenResearchManifest.model_validate(manifest).metrics["capture_count"].value == 24
    for changed in ({"finished_at": "2026-10-29T02:00:00Z"}, {"provenance": "synthetic"},
                    {"files_sha256": {"../../private": "a" * 64}}, {"unexpected": 1}):
        with pytest.raises(ValidationError):
            WardenResearchManifest.model_validate({**manifest, **changed})
