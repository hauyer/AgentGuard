import json
from datetime import UTC, datetime

from agentguard.cli import main
from agentguard.contracts.experience import ExperienceRecommendationPolicy, ExperienceRecord
from agentguard.memory.store import ExperienceStore


def test_cli_run_from_json_config_saves_reproducible_report(tmp_path, monkeypatch, capsys):
    config = tmp_path / "run.json"
    config.write_text(json.dumps({"scenario_id": "demo_cloud_security", "runtime_backend": "mock",
                                  "auto_intervene": False, "seed": 22}), encoding="utf-8")
    monkeypatch.setenv("AGENTGUARD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("sys.argv", ["agentguard", "run", "--config", str(config)])
    main()
    captured = capsys.readouterr()
    assert "Report saved to:" in captured.err
    files = list((tmp_path / "data" / "runs").glob("*.json"))
    assert len(files) == 1
    report = json.loads(files[0].read_text(encoding="utf-8"))
    assert report["backend_mode"] == "synthetic"
    assert report["experiment_designs"]


def test_cli_experience_recommendations_use_training_only_and_cost_policy(tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "data"
    store = ExperienceStore(data_dir / "experiences.sqlite3")
    store.add(ExperienceRecord(
        scenario_id="scenario-cli",
        case_family="training-family",
        dataset_partition="training",
        risk_type="unauthorized_information_flow",
        source_system="warden",
        source_run_status="completed",
        evidence_provenance_verified=True,
        audit_run_id="cli-audit-run",
        hypothesis_summary="Test-only fixture for CLI gating.",
        action_type="message_block",
        action_summary="Block one scoped message edge.",
        outcome="success",
        evidence_grade="interventional",
        verification_status="verified",
        repetitions=2,
        evidence_refs=["artifact://cli-test-fixture"],
        validation_notes=["Unit-test fixture only."],
        metrics={"risk_reduction": 0.2},
        observed_outcome={"risk_reduction": 0.2},
        benign_utility={"task_success_rate": 0.99},
        execution_cost=0.1,
        model_version="test-model-v1",
        policy_version="test-policy-v1",
        reproducibility_hash="f" * 64,
        verified_at=datetime.now(UTC),
        allow_auto_recommendation=True,
    ))
    monkeypatch.setenv("AGENTGUARD_DATA_DIR", str(data_dir))
    monkeypatch.setattr("sys.argv", [
        "agentguard", "experience", "recommend", "--scenario-id", "scenario-cli",
        "--case-family", "heldout-cli-family", "--dataset-partition", "test",
    ])

    main()
    report = json.loads(capsys.readouterr().out)
    assert report["eligible_record_count"] == 1
    assert report["target_partition"] == "test"
    assert report["execution_authorized"] is False


def test_cli_can_restore_an_immutable_recommendation_policy(tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "data"
    store = ExperienceStore(data_dir / "experiences.sqlite3")
    store.save_policy(
        ExperienceRecommendationPolicy(policy_version="experience-cli-v2", cost_penalty=0.5),
        activate=True,
    )
    monkeypatch.setenv("AGENTGUARD_DATA_DIR", str(data_dir))
    monkeypatch.setattr("sys.argv", [
        "agentguard", "experience", "policy-rollback", "--policy-version", "experience-thompson-v1",
        "--reason", "test_rollback",
    ])

    main()
    assert json.loads(capsys.readouterr().out)["active"]["policy_version"] == "experience-thompson-v1"
