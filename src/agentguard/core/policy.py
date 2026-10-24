from typing import Literal

from pydantic import Field

from agentguard.contracts.base import ContractModel
from agentguard.contracts.capabilities import CapabilityManifest
from agentguard.contracts.intervention import InterventionRequest
from agentguard.contracts.scenario import ScenarioSpec


class PolicyDecision(ContractModel):
    status: Literal["allowed", "rejected", "unsupported", "approval_required"]
    allowed: bool
    error_code: str | None = None
    reason: str
    matched_rules: list[str] = Field(default_factory=list)


class InterventionPolicyEngine:
    """Pre-execution gate for scope, scenario constraints, capabilities and budget."""

    def evaluate(
        self,
        scenario: ScenarioSpec,
        request: InterventionRequest,
        capabilities: CapabilityManifest,
        *,
        max_interventions: int,
        interventions_already_attempted: int,
        approval_granted: bool = False,
    ) -> PolicyDecision:
        if interventions_already_attempted >= max_interventions:
            return PolicyDecision(
                status="rejected",
                allowed=False,
                error_code="BUDGET_EXCEEDED",
                reason="Intervention budget is exhausted.",
                matched_rules=["budget.max_interventions"],
            )

        if request.target_type not in scenario.allowed_targets:
            return PolicyDecision(
                status="rejected",
                allowed=False,
                error_code="POLICY_DENIED",
                reason="Target type is not allowed by the scenario contract.",
                matched_rules=["scenario.allowed_targets"],
            )

        if not capabilities.supports_intervention(request.target_type, request.operation):
            return PolicyDecision(
                status="unsupported",
                allowed=False,
                error_code="CAPABILITY_UNAVAILABLE",
                reason=(
                    f"Provider {capabilities.provider_id!r} does not declare support for "
                    f"{request.target_type!r} + {request.operation!r}."
                ),
                matched_rules=["provider.capability_manifest"],
            )

        scope = request.parameters.get("scope")
        if capabilities.backend_mode == "real" and scope != "isolated_test_environment":
            return PolicyDecision(
                status="rejected",
                allowed=False,
                error_code="ISOLATION_REQUIRED",
                reason="Real-backend interventions must target an isolated test environment.",
                matched_rules=["environment.real_backend_isolation_required"],
            )
        if scope not in {"isolated_test_environment", "synthetic_demo"}:
            return PolicyDecision(
                status="rejected",
                allowed=False,
                error_code="POLICY_DENIED",
                reason="Interventions must be explicitly scoped to an isolated test environment.",
                matched_rules=["environment.isolation_required"],
            )

        if capabilities.backend_mode == "real" and not request.authorization_ref:
            return PolicyDecision(
                status="rejected",
                allowed=False,
                error_code="AUTHORIZATION_REFERENCE_REQUIRED",
                reason="Real-backend intervention requires an authorization reference.",
                matched_rules=["authorization.reference_required"],
            )
        approval_required = (
            capabilities.backend_mode == "real"
            or (capabilities.backend_mode != "synthetic" and request.parameters.get("approval_required", False) is True)
        )
        if approval_required and not approval_granted:
            return PolicyDecision(
                status="approval_required",
                allowed=False,
                error_code="APPROVAL_REQUIRED",
                reason="This real-backend intervention requires explicit operator approval.",
                matched_rules=["approval.high_impact_action"],
            )

        return PolicyDecision(
            status="allowed",
            allowed=True,
            reason="Scenario, capability, scope and budget checks passed.",
            matched_rules=[
                "scenario.allowed_targets",
                "provider.capability_manifest",
                "environment.isolation_required",
                "budget.max_interventions",
            ],
        )
