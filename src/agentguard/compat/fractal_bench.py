"""FRACTAL-Bench scenario catalogue adapter with an optional callback seam.

The installed-catalog factory maps only public scenario metadata. Evaluation truth
stays in the benchmark's central evaluator and is not copied into ScenarioSpec.
"""

from collections.abc import Callable, Sequence

from agentguard.contracts.evaluation import EvaluationReport
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.contracts.intervention import InterventionResult
from agentguard.contracts.observation import Observation
from agentguard.contracts.scenario import ScenarioSpec


class IntegrationNotConfigured(RuntimeError):
    pass


class FractalBenchAdapter:
    """Scenario catalogue adapter; benchmark truth remains in the central evaluator."""

    @classmethod
    def from_installed(cls) -> "FractalBenchAdapter":
        """Create a catalogue adapter from the installed FRACTAL-Bench package."""
        try:
            from fractal_bench.scenarios.catalog import SCENARIOS, get_scenario_entry
        except ImportError as exc:
            raise IntegrationNotConfigured(
                "FRACTAL-Bench is not importable. Install the local repository first, "
                "for example: python -m pip install -e ../FRACTAL-Bench"
            ) from exc

        track_map = {"core": "core", "transfer": "transfer", "stego": "stego"}

        def load(scenario_id: str) -> ScenarioSpec:
            try:
                entry = get_scenario_entry(scenario_id)
            except KeyError as exc:
                raise KeyError(f"Unknown FRACTAL-Bench scenario_id: {scenario_id}") from exc
            return ScenarioSpec(
                scenario_id=entry.scenario_id,
                name=entry.title,
                track=track_map.get(entry.tier, "application"),
                description=(
                    f"FRACTAL-Bench scenario: {entry.title}. "
                    f"Interaction pattern: {entry.interaction_pattern}."
                ),
                allowed_targets=["message", "tool_result"],
                public_context={
                    "scenario_id": entry.scenario_id,
                    "interaction_pattern": entry.interaction_pattern,
                    "tier": entry.tier,
                },
                metadata={
                    "provider": "FRACTAL-Bench",
                    "integration_mode": "installed_catalog",
                    "truth_visibility": "central_evaluator_only",
                },
            )

        def list_all() -> list[ScenarioSpec]:
            return [load(entry.scenario_id) for entry in SCENARIOS]

        return cls(
            scenario_loader=load,
            scenario_lister=list_all,
        )

    def __init__(
        self,
        scenario_loader: Callable[[str], ScenarioSpec] | None = None,
        scenario_lister: Callable[[], list[ScenarioSpec]] | None = None,
        evaluator: Callable[
            [ScenarioSpec, ExperimentRequest, Sequence[Observation], InterventionResult | None],
            EvaluationReport
        ] | None = None,
    ) -> None:
        self._scenario_loader = scenario_loader
        self._scenario_lister = scenario_lister
        self._evaluator = evaluator

    def get_scenario(self, scenario_id: str) -> ScenarioSpec:
        if self._scenario_loader is None:
            raise IntegrationNotConfigured(
                "FRACTAL-Bench adapter needs a scenario_loader for the pinned repository revision."
            )
        return self._scenario_loader(scenario_id)

    def list_scenarios(self) -> list[ScenarioSpec]:
        if self._scenario_lister is None:
            raise IntegrationNotConfigured(
                "FRACTAL-Bench adapter needs a scenario_lister callback."
            )
        return self._scenario_lister()

    def evaluate(
        self,
        scenario: ScenarioSpec,
        request: ExperimentRequest,
        observations: Sequence[Observation],
        intervention_result: InterventionResult | None,
    ) -> EvaluationReport:
        if self._evaluator is None:
            raise IntegrationNotConfigured(
                "FRACTAL-Bench adapter needs an evaluator callback; keep central truth isolated."
            )
        return self._evaluator(scenario, request, observations, intervention_result)
