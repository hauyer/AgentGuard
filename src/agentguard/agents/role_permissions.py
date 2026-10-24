"""Allowlisted coordination messages and internal role tools.

The optional LLM advisor receives no tools. This policy also guards deterministic
role operations so a future tool bridge cannot silently broaden role authority.
"""

from types import MappingProxyType

ROLE_TOOL_ALLOWLIST = MappingProxyType({
    "supervisor": frozenset({"delegate_trace_analysis", "request_investigation", "finish_run"}),
    "trace_analyst": frozenset({"analyze_observations"}),
    "causal_investigator": frozenset({"rank_candidates"}),
    "memory_agent": frozenset({"rank_verified_experiences"}),
    "defense_planner": frozenset({"propose_defense"}),
    "policy_engine": frozenset({"evaluate_action"}),
    "runtime": frozenset({"execute_authorized_intervention"}),
    "evaluator": frozenset({"evaluate_run"}),
    "verifier": frozenset({"summarize_verification"}),
})

ROLE_MESSAGE_ALLOWLIST = MappingProxyType({
    ("supervisor", "trace_analyst"): frozenset({"analyze_observations"}),
    ("supervisor", "causal_investigator"): frozenset({"select_next_investigation"}),
    ("supervisor", "supervisor"): frozenset({"run_finished"}),
    ("trace_analyst", "supervisor"): frozenset({"findings_ready"}),
    ("causal_investigator", "supervisor"): frozenset({
        "strategy_started", "candidate_ranked", "llm_investigation_advice",
        "candidate_selected", "no_candidate_selected",
    }),
    ("causal_investigator", "defense_planner"): frozenset({"propose_targeted_defense"}),
    ("defense_planner", "supervisor"): frozenset({"defense_proposed"}),
    ("policy_engine", "supervisor"): frozenset({"policy_decision"}),
    ("runtime", "verifier"): frozenset({"intervention_result"}),
    ("evaluator", "verifier"): frozenset({"evaluation_result"}),
    ("verifier", "supervisor"): frozenset({"verification_summary"}),
})


def authorize_role_tool(role: str, tool_name: str) -> None:
    """Raise when a role attempts an operation outside its explicit allowlist."""
    if tool_name not in ROLE_TOOL_ALLOWLIST.get(role, frozenset()):
        raise PermissionError(f"Role {role!r} is not allowed to use tool {tool_name!r}.")


def authorize_role_message(sender: str, recipient: str, message_type: str) -> None:
    """Reject role messages that are not part of the coordinator protocol."""
    allowed = ROLE_MESSAGE_ALLOWLIST.get((sender, recipient), frozenset())
    if message_type not in allowed:
        raise PermissionError(
            f"Message {message_type!r} is not allowed from {sender!r} to {recipient!r}."
        )
