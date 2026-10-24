from agentguard.contracts.intervention import InterventionRequest
from agentguard.contracts.observation import DetectionFinding


class InvestigationPlanner:
    """Select a high-priority candidate from observed findings under a fixed budget."""

    def select(self, findings: list[DetectionFinding]) -> DetectionFinding | None:
        if not findings:
            return None
        severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        return max(findings, key=lambda f: (severity_rank[f.severity], f.score))


class DefensePlanner:
    """Conservative strategy selector; never executes production changes itself."""

    def propose(
        self, finding: DetectionFinding, experiment_id: str, intervention_id: str
    ) -> InterventionRequest:
        if finding.target_type == "message" or finding.target_type == "tool_call":
            operation = "block"
        elif finding.target_type == "tool_result":
            operation = "replace"
        else:
            operation = "patch"
        return InterventionRequest(
            experiment_id=experiment_id,
            intervention_id=intervention_id,
            target_type=finding.target_type,
            target_ref=finding.target_ref,
            operation=operation,
            control_group="matched_control_v1",
            parameters={
                "scope": "isolated_test_environment",
                "approval_required": True,
                "reason": "targeted intervention proposed from a detected finding",
            },
        )
