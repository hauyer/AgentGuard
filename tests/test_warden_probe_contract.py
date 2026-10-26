import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentguard.contracts.warden_probe import WardenWhiteboxProbeExperimentConfig

ROOT = Path(__file__).resolve().parents[1]


def _config():
    return {
        "schema_version": "warden.whitebox-probe-experiment/1",
        "experiment_id": "probe-smoke-contract",
        "model_id": "org/model",
        "model": {
            "model_path": "models/model-snapshot",
            "tokenizer_path": "tokenizer/tokenizer.json",
            "revision": "commit-sha",
            "device": "cuda",
            "dtype": "bfloat16",
            "trust_remote_code": False,
            "local_files_only": True,
            "use_safetensors": True,
            "parameter_gradients": False,
            "quantization": "none",
        },
        "dataset_path": "data/interactions.jsonl",
        "output_directory": "runs/probe-1",
        "agent_ids": ["sender", "receiver"],
        "capture_targets": [{
            "target_id": "resid-post-8",
            "agent_ids": ["sender", "receiver"],
            "layer": 8,
            "component": "resid_post",
            "token_policy": "last_token",
            "retain_gradient": False,
        }],
        "probe": {"c": 1.0, "max_iter": 2000, "random_seed": 42},
    }


def test_probe_config_contract_matches_pinned_warden_schema_snapshot():
    snapshot_path = ROOT / "schemas/warden-whitebox-probe-experiment-1.upstream.schema.json"
    raw = snapshot_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "cee132eed53bf81d64da68e68bcda881f0aa31f246bc33d6a96a86ccdc1fb0ae"
    )
    upstream = json.loads(raw)
    local_schema = WardenWhiteboxProbeExperimentConfig.model_json_schema()
    assert set(upstream["properties"]) == set(local_schema["properties"])
    assert set(upstream["required"]) == set(local_schema["required"])
    assert upstream["properties"]["schema_version"]["const"] == "warden.whitebox-probe-experiment/1"
    assert upstream["$defs"]["model"]["properties"]["quantization"]["enum"] == [
        "none", "bnb_4bit", "bnb_8bit",
    ]
    assert upstream["$defs"]["captureTarget"]["properties"]["retain_gradient"]["const"] is False


def test_probe_config_captures_model_data_split_and_training_identity():
    value = WardenWhiteboxProbeExperimentConfig.model_validate(_config())
    assert value.model.revision == "commit-sha"
    assert value.model.tokenizer_path == "tokenizer/tokenizer.json"
    assert value.model.quantization == "none"
    assert value.capture_targets[0].layer == 8
    assert value.capture_targets[0].component == "resid_post"
    assert value.capture_targets[0].token_policy == "last_token"
    assert value.probe.random_seed == 42
    assert value.config_sha256() == WardenWhiteboxProbeExperimentConfig.model_validate(
        _config()
    ).config_sha256()
    changed = _config()
    changed["probe"]["random_seed"] = 43
    assert value.config_sha256() != WardenWhiteboxProbeExperimentConfig.model_validate(
        changed
    ).config_sha256()


@pytest.mark.parametrize("change", [
    {"model": {"model_path": "model", "parameter_gradients": True}},
    {"capture_targets": [{"target_id": "missing-coordinate", "agent_ids": ["a"]}]},
    {"capture_targets": [{"target_id": "gradient", "agent_ids": ["a"], "layer": 1,
                          "retain_gradient": True}]},
    {"agent_ids": ["sender", "sender"]},
    {"unknown_field": True},
])
def test_probe_config_rejects_noncontract_fields_and_unsupported_execution_options(change):
    config = _config()
    config.update(change)
    with pytest.raises(ValidationError):
        WardenWhiteboxProbeExperimentConfig.model_validate(config)

