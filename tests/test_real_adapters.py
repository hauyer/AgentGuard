import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from agentguard.adapters.fractal_bench import FractalBenchAdapter
from agentguard.adapters.stegopot import StegoPotAdapter
from agentguard.contracts.experiment import ExperimentRequest


def test_fractal_catalog_adapter_uses_public_catalog_without_truth(monkeypatch):
    fractal_bench = ModuleType("fractal_bench")
    scenarios_module = ModuleType("fractal_bench.scenarios")
    catalog_module = ModuleType("fractal_bench.scenarios.catalog")
    entries = [
        SimpleNamespace(
            scenario_id="release_review",
            tier="core",
            title="Release review",
            interaction_pattern="evidence nodes converge",
        )
    ]
    catalog_module.SCENARIOS = entries
    catalog_module.get_scenario_entry = lambda scenario_id: next(
        entry for entry in entries if entry.scenario_id == scenario_id
    )
    monkeypatch.setitem(sys.modules, "fractal_bench", fractal_bench)
    monkeypatch.setitem(sys.modules, "fractal_bench.scenarios", scenarios_module)
    monkeypatch.setitem(sys.modules, "fractal_bench.scenarios.catalog", catalog_module)

    adapter = FractalBenchAdapter.from_installed()
    scenario = adapter.get_scenario("release_review")
    assert scenario.name == "Release review"
    assert scenario.public_context["interaction_pattern"] == "evidence nodes converge"
    assert "truth" not in scenario.model_dump()
    assert len(adapter.list_scenarios()) == 1


def test_stegopot_config_adapter_runs_file_api_and_maps_observations(tmp_path):
    config_path = tmp_path / "experiment.json"
    config_path.write_text(json.dumps({
        "schema_version": "1",
        "plugins": ["fractal_bench"],
        "seed": 1,
        "scenario": {
            "type": "fractal_bench.fractal",
            "config": {
                "scenario_id": "release_review",
                "families": 2,
                "policy_type": "core.llm",
                "policy_config": {"temperature": 0},
                "max_rounds": 4,
                "schedule_mode": "automatic"
            }
        }
    }), encoding="utf-8")

    captured = {}

    def fake_run_file(*, config, workspace, output, load_env):
        captured["config"] = json.loads(Path(config).read_text(encoding="utf-8"))
        captured["workspace"] = Path(workspace)
        captured["output"] = Path(output)
        captured["load_env"] = load_env
        report = {
            "status": "completed",
            "summary": {
                "metrics": {
                    "fractal_bench.evaluator": {
                        "intention_to_treat": {
                            "completion_rate": 1.0,
                            "benign_accuracy": 0.75,
                            "coordinated_target_rate": 0.25,
                        },
                        "per_protocol": {
                            "benign_accuracy": 0.8,
                        },
                    }
                }
            },
            "trials": [{
                "trial": {"trial_id": "trial-1"},
                "status": "completed",
                # Deliberately include a truth field; the adapter must not read it.
                "truth": {"oracle_secret": "do-not-expose"},
                "result": {
                    "messages": [{
                        "message_id": "m-1",
                        "sender": "agent-a",
                        "recipient": "agent-b",
                        "content": "Observed message text",
                        "round_index": 2,
                    }],
                    "final_answers": {"agent-b": "candidate-x"},
                }
            }]
        }
        run_dir = Path(output) / "fake-run"
        run_dir.mkdir(parents=True)
        return report, run_dir

    adapter = StegoPotAdapter.from_config(
        config_path,
        workspace=tmp_path,
        output_dir=tmp_path / "outputs",
        run_file_callable=fake_run_file,
    )
    scenario = type("Scenario", (), {"scenario_id": "supply_chain_dispatch"})()
    request = ExperimentRequest(
        experiment_id="exp-real-adapter-test",
        scenario_id="supply_chain_dispatch",
        seed=91,
        families=3,
        runtime_backend="stegopot",
    )
    observations = adapter.execute(scenario, request)

    assert captured["config"]["seed"] == 91
    assert captured["config"]["scenario"]["config"]["scenario_id"] == "supply_chain_dispatch"
    assert captured["config"]["scenario"]["config"]["families"] == 3
    assert captured["config"]["runtime"]["max_seconds"] == request.max_runtime_seconds
    assert captured["config"]["runtime"]["max_model_calls"] == request.max_model_calls
    assert captured["config"]["runtime"]["max_output_tokens"] == request.max_output_tokens_per_call
    assert captured["config"]["runtime"]["max_trials"] == request.families * 8
    assert len(observations) == 2
    message = observations[0]
    assert message.event_type == "message"
    assert message.source == "agent-a"
    assert message.target == "agent-b"
    assert message.summary == "Observed message text"
    assert all("oracle_secret" not in item.model_dump_json() for item in observations)

    evaluation = adapter.evaluate(
        scenario, request, observations, intervention_result=None
    )
    assert evaluation.evidence_mode == "synthetic"  # injected test runner is not empirical
    assert evaluation.post_intervention_metrics["benign_accuracy"].value == 0.75
    assert evaluation.post_intervention_metrics["per_protocol_benign_accuracy"].value == 0.8


def test_stegopot_adapter_requires_paired_intervention_callback():
    adapter = StegoPotAdapter()
    from agentguard.contracts.intervention import InterventionRequest
    result = adapter.intervene(InterventionRequest(
        experiment_id="exp-1",
        intervention_id="int-1",
        target_type="message",
        target_ref="msg-1",
        operation="block",
        parameters={"scope": "isolated_test_environment"},
    ))
    assert result.status == "unsupported"
    assert result.error_code == "CAPABILITY_UNAVAILABLE"
