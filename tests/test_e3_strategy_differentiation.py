"""E3: Strategy differentiation under budget constraints.

D5 proved the evaluation framework runs; E3 proves the strategies actually
behave differently under a tight budget, where the first action choice matters.
"""
from __future__ import annotations

from agentguard.contracts.investigation_loop import LoopAction
from agentguard.services.investigation_strategies import select_next_action


def _action(action_type: str) -> LoopAction:
    return LoopAction(
        action_id=f"act-{action_type}",
        action_type=action_type,
        description=f"action: {action_type}",
        cost_estimate=1.0,
    )


def _all_actions() -> list[LoopAction]:
    return [
        _action("read_state"),
        _action("read_graph"),
        _action("read_evidence"),
        _action("read_hypotheses"),
        _action("read_experiments"),
        _action("read_experiences"),
        _action("submit_observation_audit"),
    ]


class TestE3StrategyDifferences:
    """Different strategies must pick different first actions under tight budget."""

    @staticmethod
    def _type_of(action_id: str) -> str:
        return action_id.removeprefix("act-")

    def test_first_step_differs_when_evidence_missing(self):
        """Graph already present, evidence absent -> info_value picks evidence first.

        Fixed_order would walk read_state -> read_graph -> read_evidence;
        information_value jumps straight to evidence because it's the biggest gap.
        """
        actions = _all_actions()
        evidence_summary = {
            "graph_available": True,
            "evidence_items": [],
            "findings": [],
            "hypotheses": [],
            "kernel_configured": False,
        }
        fixed = select_next_action(
            "fixed_order", taken_types=set(), actions=actions,
            evidence_summary=evidence_summary, remaining_budget={"actions": 3},
        )
        info = select_next_action(
            "information_value", taken_types=set(), actions=actions,
            evidence_summary=evidence_summary, remaining_budget={"actions": 3},
        )
        assert self._type_of(fixed.action_id) == "read_state"
        assert self._type_of(info.action_id) == "read_evidence"
        assert fixed.action_id != info.action_id

    def test_risk_score_prefers_observation_audit_when_kernel_configured(self):
        """Risk-first strategy prioritizes a fresh observation over passive reads."""
        actions = _all_actions()
        evidence_summary = {
            "graph_available": True,
            "evidence_items": ["ev-1"],
            "findings": [{"risk_type": "secret_flow"}],
            "kernel_configured": True,
        }
        risk = select_next_action(
            "risk_score", taken_types=set(), actions=actions,
            evidence_summary=evidence_summary, remaining_budget={"actions": 3},
        )
        fixed = select_next_action(
            "fixed_order", taken_types=set(), actions=actions,
            evidence_summary=evidence_summary, remaining_budget={"actions": 3},
        )
        assert self._type_of(risk.action_id) == "submit_observation_audit"
        assert self._type_of(fixed.action_id) == "read_state"

    def test_information_value_weights_bigger_gaps_higher(self):
        """Missing evidence and missing graph are both weight-3, but missing
        evidence is a bigger gap when findings exist without hypotheses."""
        actions = _all_actions()
        evidence_summary = {
            "graph_available": True,
            "evidence_items": ["ev-1"],
            "findings": [{"risk_type": "secret_flow"}],
            "hypotheses": [],  # findings exist but no hypotheses -> high gap
            "kernel_configured": False,
        }
        choice = select_next_action(
            "information_value", taken_types=set(), actions=actions,
            evidence_summary=evidence_summary, remaining_budget={"actions": 2},
        )
        # read_hypotheses has weight 3 when findings exist without hypotheses
        # vs read_graph weight 1 when graph is already available
        assert self._type_of(choice.action_id) == "read_hypotheses"

    def test_budget_limited_first_round_determines_what_is_collected(self):
        """With max_actions=2, the first action choice determines what gets seen.

        fixed_order picks read_state first (low value), info_value picks the
        highest-value gap first -> the loop ends with different information.
        """
        actions = _all_actions()
        evidence_summary = {
            "graph_available": False,
            "evidence_items": [],
            "findings": [],
            "kernel_configured": False,
        }
        budget = {"actions": 2, "model_calls": 0}
        fixed_round1 = select_next_action(
            "fixed_order", taken_types=set(), actions=actions,
            evidence_summary=evidence_summary, remaining_budget=budget,
        )
        info_round1 = select_next_action(
            "information_value", taken_types=set(), actions=actions,
            evidence_summary=evidence_summary, remaining_budget=budget,
        )
        # Round 2: fixed_order now has read_state taken, info_value has its gap taken
        fixed_round2 = select_next_action(
            "fixed_order", taken_types={fixed_round1.action_id.split("-")[1]},
            actions=actions, evidence_summary=evidence_summary, remaining_budget={"actions": 1},
        )
        # info_value round 2: its highest gap (read_graph weight 3) is now taken
        # -> next highest gap
        info_round2 = select_next_action(
            "information_value", taken_types={info_round1.action_id.split("-")[1]},
            actions=actions, evidence_summary=evidence_summary, remaining_budget={"actions": 1},
        )
        # Both strategies collected exactly 2 different actions
        fixed_set = {fixed_round1.action_id, fixed_round2.action_id}
        info_set = {info_round1.action_id, info_round2.action_id}
        # They differ because first-step choices diverge
        assert len(fixed_set) == 2 and len(info_set) == 2
        # fixed_order always starts with read_state
        assert self._type_of(fixed_round1.action_id) == "read_state"
        # info_value starts with the biggest gap (read_graph weight 3)
        assert self._type_of(info_round1.action_id) == "read_graph"

    def test_experience_strategy_degrades_when_no_verified_records(self):
        """Experience strategy falls back to information_value when no experiences exist."""
        actions = _all_actions()
        evidence_summary = {
            "graph_available": True,
            "evidence_items": ["ev-1"],
            "findings": [],
            "experiences": [],  # no verified experiences
            "kernel_configured": False,
        }
        choice = select_next_action(
            "experience", taken_types=set(), actions=actions,
            evidence_summary=evidence_summary, remaining_budget={"actions": 3},
        )
        assert choice.degraded is True
        assert "fell back to information_value" in choice.degradation_note

    def test_llm_degrades_when_no_client(self):
        """LLM strategy falls back deterministically when no client is configured."""
        actions = _all_actions()
        evidence_summary = {
            "graph_available": True,
            "evidence_items": ["ev-1"],
            "findings": [],
            "kernel_configured": False,
        }
        choice = select_next_action(
            "llm", taken_types=set(), actions=actions,
            evidence_summary=evidence_summary, remaining_budget={"actions": 3, "model_calls": 1},
            llm_client=None,
        )
        assert choice.degraded is True
        assert "no configured client" in choice.degradation_note

    def test_heuristic_not_information_gain_labeled_correctly(self):
        """Information-value ranking is heuristic, not calibrated information gain.

        The decision reason uses 'information value' heuristic language, not
        'information gain' or 'expected uncertainty reduction'.
        """
        actions = _all_actions()
        evidence_summary = {
            "graph_available": False,
            "evidence_items": [],
            "findings": [],
            "kernel_configured": False,
        }
        choice = select_next_action(
            "information_value", taken_types=set(), actions=actions,
            evidence_summary=evidence_summary, remaining_budget={"actions": 3},
        )
        assert "information value" in choice.reason
        assert "information gain" not in choice.reason.lower()
        assert "expected uncertainty" not in choice.reason.lower()
