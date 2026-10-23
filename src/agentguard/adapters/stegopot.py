"""StegoPot runtime integration.

Supports a real integration through StegoPot's documented ``run_file`` API when
an experiment config is supplied. Direct post-run intervention is intentionally
unsupported unless an explicit intervention callback is configured.
"""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from threading import RLock

from agentguard.contracts.capabilities import CapabilityManifest
from agentguard.contracts.evaluation import EvaluationReport, MetricValue
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.contracts.intervention import InterventionRequest, InterventionResult
from agentguard.contracts.observation import Observation
from agentguard.contracts.scenario import ScenarioSpec
from agentguard.core.features import extract_message_features


class IntegrationNotConfigured(RuntimeError):
    pass


class StegoPotAdapter:
    """Adapter for StegoPot's file-level experiment API and controlled callback seams."""

    backend_mode = "real"

    def __init__(
        self,
        executor: Callable[[ScenarioSpec, ExperimentRequest], list[Observation]] | None = None,
        intervention_executor: Callable[[InterventionRequest], InterventionResult] | None = None,
        evaluation_executor: Callable[
            [ScenarioSpec, ExperimentRequest, list[Observation], InterventionResult | None],
            EvaluationReport
        ] | None = None,
        capability_manifest: CapabilityManifest | None = None,
        *,
        config_path: str | Path | None = None,
        workspace: str | Path = ".",
        output_dir: str | Path | None = None,
        run_file_callable: Callable[..., tuple[dict[str, Any], Path]] | None = None,
    ) -> None:
        self._executor = executor
        self._intervention_executor = intervention_executor
        self._evaluation_executor = evaluation_executor
        self._capability_manifest = capability_manifest
        self.workspace = Path(workspace).expanduser().resolve()
        if config_path:
            config_value = Path(config_path).expanduser()
            self.config_path = (
                config_value if config_value.is_absolute() else self.workspace / config_value
            ).resolve()
        else:
            self.config_path = None
        if output_dir:
            output_value = Path(output_dir).expanduser()
            self.output_dir = (
                output_value if output_value.is_absolute() else self.workspace / output_value
            ).resolve()
        else:
            self.output_dir = self.workspace / ".agentguard" / "runs"
        self._run_file_callable = run_file_callable
        self._run_results: dict[str, tuple[dict[str, Any], Path, bool]] = {}
        self._run_results_lock = RLock()

    @classmethod
    def from_config(
        cls,
        config_path: str | Path,
        *,
        workspace: str | Path = ".",
        output_dir: str | Path | None = None,
        run_file_callable: Callable[..., tuple[dict[str, Any], Path]] | None = None,
        intervention_executor: Callable[[InterventionRequest], InterventionResult] | None = None,
        evaluation_executor: Callable[
            [ScenarioSpec, ExperimentRequest, list[Observation], InterventionResult | None],
            EvaluationReport
        ] | None = None,
        capability_manifest: CapabilityManifest | None = None,
    ) -> "StegoPotAdapter":
        """Create an adapter that executes an installed StegoPot experiment config."""
        return cls(
            config_path=config_path,
            workspace=workspace,
            output_dir=output_dir,
            run_file_callable=run_file_callable,
            intervention_executor=intervention_executor,
            evaluation_executor=evaluation_executor,
            capability_manifest=capability_manifest,
        )

    def capabilities(self) -> CapabilityManifest:
        if self._capability_manifest is not None:
            return self._capability_manifest
        if self._intervention_executor is not None:
            return CapabilityManifest(
                provider_id="stegopot",
                provider_version="2.x-configured-callback",
                backend_mode="real",
                capabilities={
                    "message_trace": True,
                    "tool_trace": True,
                    "controlled_intervention": True,
                    "activation_read": False,
                    "activation_intervention": False,
                },
                supported_target_types=[],
                supported_operations=[],
                limitations=[
                    "An intervention callback is configured, but target/operation support is not inferred.",
                    "Pass an explicit capability_manifest to authorize specific operations.",
                    "White-box capabilities must be declared separately after verification.",
                ],
            )
        if self.config_path is not None:
            return CapabilityManifest(
                provider_id="stegopot",
                provider_version="2.1+",
                backend_mode="real",
                capabilities={
                    "message_trace": True,
                    "tool_trace": True,
                    "activation_read": False,
                    "activation_intervention": False,
                    "counterfactual_replay": False,
                },
                supported_target_types=[],
                supported_operations=[],
                limitations=[
                    "Real StegoPot experiment execution is available through run_file.",
                    "Direct post-run interventions are not enabled by this adapter.",
                    "Activation access is not inferred from a config file.",
                ],
            )
        return CapabilityManifest(
            provider_id="stegopot",
            provider_version="unverified",
            backend_mode="real",
            capabilities={},
            supported_target_types=[],
            supported_operations=[],
            limitations=[
                "No config_path, executor, or verified capability manifest was supplied.",
                "This adapter is not configured for runtime execution.",
            ],
        )

    def execute(
        self, scenario: ScenarioSpec, request: ExperimentRequest
    ) -> list[Observation]:
        if self._executor is not None:
            observations = self._executor(scenario, request)
            return observations
        if self.config_path is None:
            raise IntegrationNotConfigured(
                "StegoPot adapter requires an executor callback or config_path."
            )
        report, directory = self._run_configured_experiment(scenario, request)
        # An injected runner is treated as a test seam, not empirical evidence.
        is_real_run = self._run_file_callable is None
        with self._run_results_lock:
            self._run_results[request.experiment_id] = (report, directory, is_real_run)
            while len(self._run_results) > 256:
                self._run_results.pop(next(iter(self._run_results)))
        return self._observations_from_report(report, directory, request)

    def intervene(self, request: InterventionRequest) -> InterventionResult:
        if self._intervention_executor is not None:
            return self._intervention_executor(request)
        return InterventionResult(
            intervention_id=request.intervention_id,
            status="unsupported",
            changed=False,
            effect_status="not_measured",
            message=(
                "This adapter cannot mutate a completed StegoPot run. Implement a paired "
                "experiment or provide an explicitly tested intervention_executor callback."
            ),
            error_code="CAPABILITY_UNAVAILABLE",
        )

    def evaluate(
        self,
        scenario: ScenarioSpec,
        request: ExperimentRequest,
        observations: list[Observation],
        intervention_result: InterventionResult | None,
    ) -> EvaluationReport:
        """Map FRACTAL-Bench's central evaluator summary from the configured run."""
        if self._evaluation_executor is not None:
            return self._evaluation_executor(
                scenario, request, observations, intervention_result
            )
        with self._run_results_lock:
            run_result = self._run_results.get(request.experiment_id)
        if run_result is None:
            raise IntegrationNotConfigured(
                "No StegoPot report is available for this experiment_id. Run execute() first."
            )
        run_report, _run_directory, is_real_run = run_result
        metrics_by_component = (
            run_report.get("summary", {}).get("metrics", {})
        )
        benchmark_metrics = metrics_by_component.get("fractal_bench.evaluator")
        if not isinstance(benchmark_metrics, dict):
            return EvaluationReport(
                experiment_id=request.experiment_id,
                status="partial",
                evidence_mode="observed" if is_real_run else "synthetic",
                notes=[
                    "The StegoPot report did not contain the FRACTAL-Bench central evaluator output.",
                    "Ensure the experiment config loads the FRACTAL-Bench plugin and evaluator.",
                ],
            )

        itt = benchmark_metrics.get("intention_to_treat") or {}
        per_protocol = benchmark_metrics.get("per_protocol") or {}
        selected = itt if isinstance(itt, dict) else {}
        metrics: dict[str, MetricValue] = {}
        for name in (
            "completion_rate",
            "valid_action_rate",
            "protocol_valid_rate",
            "strict_protocol_valid_rate",
            "benign_accuracy",
            "coordinated_target_rate",
            "mean_benign_utility",
        ):
            value = selected.get(name)
            if isinstance(value, (int, float)):
                metrics[name] = MetricValue(
                    name=name,
                    value=float(value),
                    unit="normalized",
                    source="FRACTAL-Bench central evaluator",
                    evidence_mode="measured" if is_real_run else "synthetic",
                    description="Intention-to-treat benchmark metric, aggregated by case family.",
                )
            pp_value = per_protocol.get(name) if isinstance(per_protocol, dict) else None
            if isinstance(pp_value, (int, float)):
                key = f"per_protocol_{name}"
                metrics[key] = MetricValue(
                    name=key,
                    value=float(pp_value),
                    unit="normalized",
                    source="FRACTAL-Bench central evaluator",
                    evidence_mode="measured" if is_real_run else "synthetic",
                    description="Per-protocol benchmark metric; shown separately from ITT.",
                )

        run_status = str(run_report.get("status", "partial"))
        notes = [
            "Metrics are taken from FRACTAL-Bench's central evaluator, not inferred by the detector.",
            "Intention-to-treat and per-protocol metrics are reported separately.",
            "This report does not establish a causal effect of an AgentGuard intervention.",
        ]
        if intervention_result is None:
            notes.append("No AgentGuard intervention result was supplied for this run.")
        elif intervention_result.effect_status != "measured":
            notes.append(
                "The intervention was not measured as a real post-intervention comparison; "
                "benchmark metrics must not be interpreted as intervention deltas."
            )
        return EvaluationReport(
            experiment_id=request.experiment_id,
            status="completed" if run_status == "completed" else "partial",
            evidence_mode="measured" if is_real_run else "synthetic",
            baseline_metrics={},
            post_intervention_metrics=metrics,
            deltas={},
            benign_utility_preserved=None,
            notes=notes,
        )


    def usage_for(self, experiment_id: str) -> dict[str, int | float | None]:
        """Return available usage values; missing counters remain None."""
        with self._run_results_lock:
            run_result = self._run_results.get(experiment_id)
        if run_result is None:
            return {
                "model_calls_used": None,
                "model_tokens_used": None,
                "tool_calls_used": None,
            }
        report, _directory, _is_real_run = run_result
        usage = report.get("usage", {})
        usage = usage if isinstance(usage, dict) else {}
        total_tokens = usage.get("total_tokens")
        model_calls = report.get("model_calls")
        tool_calls = report.get("tool_calls")
        return {
            "model_calls_used": model_calls if isinstance(model_calls, int) else None,
            "model_tokens_used": total_tokens if isinstance(total_tokens, int) else None,
            "tool_calls_used": tool_calls if isinstance(tool_calls, int) else None,
        }

    def _run_configured_experiment(
        self, scenario: ScenarioSpec, request: ExperimentRequest
    ) -> tuple[dict[str, Any], Path]:
        if not self.config_path or not self.config_path.is_file():
            raise IntegrationNotConfigured(
                f"StegoPot config file does not exist: {self.config_path}"
            )
        config = self._load_config(self.config_path)
        scenario_config = config.get("scenario")
        if not isinstance(scenario_config, dict) or not isinstance(
            scenario_config.get("config"), dict
        ):
            raise ValueError(
                "StegoPot config must use scenario: {type: fractal_bench.fractal, config: {...}}."
            )
        scenario_type = scenario_config.get("type")
        if scenario_type not in {"fractal_bench.fractal", "fractal_bench.release_review"}:
            raise ValueError(
                "Configured scenario must use the FRACTAL-Bench StegoPot plugin "
                "(fractal_bench.fractal or fractal_bench.release_review)."
            )
        if (
            scenario_type == "fractal_bench.release_review"
            and scenario.scenario_id != "release_review"
        ):
            raise ValueError(
                "The legacy fractal_bench.release_review alias only supports scenario_id='release_review'; "
                "use fractal_bench.fractal for other catalogue scenarios."
            )
        if "fractal_bench" not in config.get("plugins", []):
            raise ValueError(
                "StegoPot config must declare plugins: [fractal_bench] to use the real benchmark."
            )
        config["seed"] = request.seed
        scenario_config["config"]["scenario_id"] = scenario.scenario_id
        scenario_config["config"]["families"] = request.families

        runtime_config = config.setdefault("runtime", {})
        if not isinstance(runtime_config, dict):
            raise ValueError("StegoPot config runtime field must be a mapping.")
        if request.max_model_tokens <= 0:
            raise ValueError(
                "A real StegoPot model run requires max_model_tokens > 0; "
                "zero-token runs are not supported by this real-model adapter."
            )

        def cap_runtime_limit(name: str, requested: int | float) -> None:
            current = runtime_config.get(name)
            if isinstance(current, (int, float)) and current > 0:
                runtime_config[name] = min(current, requested)
            else:
                runtime_config[name] = requested

        # StegoPot enforces these at runtime (max_seconds is cooperative, not a hard kill).
        cap_runtime_limit("max_seconds", request.max_runtime_seconds)
        cap_runtime_limit("max_model_calls", request.max_model_calls)
        cap_runtime_limit(
            "max_output_tokens",
            min(request.max_output_tokens_per_call, request.max_model_tokens),
        )
        # FRACTAL-Bench creates eight factorial instances per case family.
        cap_runtime_limit("max_trials", request.families * 8)

        # Never use the untrusted experiment_id as a path component.
        generated_id = sha256(request.experiment_id.encode("utf-8")).hexdigest()[:20]
        generated_dir = self.workspace / ".agentguard" / "generated"
        generated_dir.mkdir(parents=True, exist_ok=True)
        generated_config = generated_dir / f"{generated_id}.json"
        generated_config.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        run_callable = self._run_file_callable
        if run_callable is None:
            try:
                from stegopot.bootstrap.experiments.api import run_file
            except ImportError as exc:
                raise IntegrationNotConfigured(
                    "StegoPot >=2.1 is not importable. Install the local repository first, "
                    "for example: python -m pip install -e ../stegopot"
                ) from exc
            run_callable = run_file

        self.output_dir.mkdir(parents=True, exist_ok=True)
        report, directory = run_callable(
            config=generated_config,
            workspace=self.workspace,
            output=self.output_dir,
            load_env=True,
        )
        if not isinstance(report, dict):
            raise TypeError("StegoPot run_file must return a report mapping.")
        return report, Path(directory)

    @staticmethod
    def _load_config(path: Path) -> dict[str, Any]:
        raw = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            value = json.loads(raw)
        else:
            try:
                import yaml
            except ImportError as exc:
                raise IntegrationNotConfigured(
                    "Loading YAML config requires PyYAML (installed by StegoPot). "
                    "Use a JSON config if you do not want that dependency."
                ) from exc
            value = yaml.safe_load(raw)
        if not isinstance(value, dict):
            raise ValueError("StegoPot config root must be a mapping.")
        return value

    @staticmethod
    def _observations_from_report(
        report: dict[str, Any],
        directory: Path,
        request: ExperimentRequest,
    ) -> list[Observation]:
        """Extract runtime messages and decisions only; never copy trial.truth."""
        observations: list[Observation] = []
        for trial_index, record in enumerate(report.get("trials", [])):
            if not isinstance(record, dict):
                continue
            trial = record.get("trial", {})
            trial_id = str(
                trial.get("trial_id", record.get("trial_id", f"trial-{trial_index}"))
            ) if isinstance(trial, dict) else str(record.get("trial_id", f"trial-{trial_index}"))
            result = record.get("result", {})
            result = result if isinstance(result, dict) else {}
            messages = result.get("messages", [])
            if isinstance(messages, list):
                for message_index, message in enumerate(messages):
                    if not isinstance(message, dict):
                        continue
                    sender = str(message.get("sender", message.get("sender_id", "unknown")))
                    recipient = str(message.get("recipient", message.get("receiver_id", "unknown")))
                    content = str(message.get("content", ""))
                    message_id = str(
                        message.get("message_id")
                        or f"{trial_id}-message-{message_index}"
                    )
                    round_index = message.get("round_index", message.get("round", 0))
                    if not isinstance(round_index, int):
                        round_index = 0
                    observations.append(
                        Observation(
                            experiment_id=request.experiment_id,
                            actor_id=sender,
                            event_type="message",
                            source=sender,
                            target=recipient,
                            target_ref=f"{trial_id}:{message_id}",
                            summary=content[:4000],
                            features={
                                "trial_id": trial_id,
                                "message_id": message_id,
                                "round_index": round_index,
                                **extract_message_features(content),
                            },
                            visibility="research_only",
                            provenance=str(directory / trial_id / "result.json"),
                        )
                    )
            final_answers = result.get("final_answers", {})
            if isinstance(final_answers, dict):
                for actor, answer in final_answers.items():
                    observations.append(
                        Observation(
                            experiment_id=request.experiment_id,
                            actor_id=str(actor),
                            event_type="decision",
                            target_ref=f"{trial_id}:decision:{actor}",
                            summary=str(answer)[:4000],
                            features={"trial_id": trial_id},
                            visibility="research_only",
                            provenance=str(directory / trial_id / "result.json"),
                        )
                    )
            if not messages and not final_answers:
                observations.append(
                    Observation(
                        experiment_id=request.experiment_id,
                        actor_id="stegopot_runtime",
                        event_type="runtime_status",
                        target_ref=f"{trial_id}:status",
                        summary=f"Trial ended with status={record.get('status', 'unknown')}.",
                        features={
                            "trial_id": trial_id,
                            "completed": record.get("status") == "completed",
                        },
                        visibility="research_only",
                        provenance=str(directory / trial_id / "result.json"),
                    )
                )
        return observations
