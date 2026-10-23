from typing import Protocol, Sequence
from agentguard.contracts.scenario import ScenarioSpec
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.contracts.observation import Observation
from agentguard.contracts.intervention import InterventionRequest, InterventionResult
from agentguard.contracts.evaluation import EvaluationReport
from agentguard.contracts.capabilities import CapabilityManifest


class ScenarioProvider(Protocol):
    def get_scenario(self, scenario_id: str) -> ScenarioSpec: ...
    def list_scenarios(self) -> list[ScenarioSpec]: ...


class RuntimeProvider(Protocol):
    backend_mode: str

    def capabilities(self) -> CapabilityManifest: ...

    def execute(
        self, scenario: ScenarioSpec, request: ExperimentRequest
    ) -> list[Observation]: ...

    def intervene(self, request: InterventionRequest) -> InterventionResult: ...


class EvaluatorProvider(Protocol):
    def evaluate(
        self,
        scenario: ScenarioSpec,
        request: ExperimentRequest,
        observations: Sequence[Observation],
        intervention_result: InterventionResult | None,
    ) -> EvaluationReport: ...
