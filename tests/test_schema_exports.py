import json
from pathlib import Path

from scripts.export_schemas import SCHEMAS


def test_exported_schemas_match_current_public_contracts():
    schema_dir = Path(__file__).resolve().parents[1] / "schemas"
    for name, model in SCHEMAS.items():
        assert json.loads((schema_dir / name).read_text(encoding="utf-8")) == (
            model.model_json_schema()
        ), name


def test_checked_in_schemas_exist_and_are_valid_json():
    root = Path(__file__).resolve().parents[1]
    schema_dir = root / "schemas"
    expected = {
        "scenario.schema.json",
        "experiment.schema.json",
        "observation.schema.json",
        "finding.schema.json",
        "intervention-request.schema.json",
        "intervention-result.schema.json",
        "evaluation.schema.json",
        "evidence.schema.json",
        "capabilities.schema.json",
        "agent-message.schema.json",
        "run-report.schema.json",
        "experiment-budget.schema.json",
        "investigation-hypothesis.schema.json",
        "investigation-action.schema.json",
        "paired-experiment-design.schema.json",
        "defense-candidate.schema.json",
        "defense-preview.schema.json",
        "defense-verification.schema.json",
        "experience-record.schema.json",
        "experience-search-result.schema.json",
        "experience-bandit-action-score.schema.json",
        "experience-bandit-recommendation-report.schema.json",
        "artifact-ref.schema.json",
        "artifact-metadata.schema.json",
        "run-timeline-event.schema.json",
        "budget-usage.schema.json",
        "interaction-node.schema.json",
        "interaction-edge.schema.json",
        "candidate-path.schema.json",
        "interaction-graph-report.schema.json",
        "binary-probe-train-request.schema.json",
        "binary-probe-training-report.schema.json",
        "binary-probe-predict-request.schema.json",
        "binary-probe-prediction-report.schema.json",
        "paired-experiment-run-request.schema.json",
        "paired-experiment-pair-result.schema.json",
        "paired-experiment-execution-report.schema.json",
        "study-registration-request.schema.json",
        "registered-study.schema.json",
        "study-outcome-pairs.schema.json",
        "study-analysis-request.schema.json",
        "study-analysis-report.schema.json",
        "attribution-node-analysis-request.schema.json",
        "attribution-node-analysis-report.schema.json",
        "path-shift-analysis-request.schema.json",
        "path-shift-analysis-report.schema.json",
        "activation-patch-spec.schema.json",
    }
    assert expected <= {path.name for path in schema_dir.glob("*.json")}
    for name in expected:
        payload = json.loads((schema_dir / name).read_text(encoding="utf-8"))
        assert payload.get("title")
        assert payload.get("type") == "object"
        assert "properties" in payload
