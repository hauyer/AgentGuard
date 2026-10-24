from agentguard.contracts.observation import DetectionFinding, Observation


class RuleBasedDetector:
    """Transparent screening baseline; findings are leads for investigation, not proof."""

    def detect(self, observations: list[Observation]) -> list[DetectionFinding]:
        findings: list[DetectionFinding] = []
        for obs in observations:
            policy_violation = obs.features.get("policy_violation") is True
            authorization_denied = obs.features.get("authorization") == "denied"
            anomaly = obs.features.get("anomaly_score", 0.0)
            high_anomaly = isinstance(anomaly, (int, float)) and anomaly >= 0.85
            encoded_payload = (
                obs.features.get("base64_like_run") is True
                or obs.features.get("hex_like_run") is True
            )
            if not (policy_violation or authorization_denied or high_anomaly or encoded_payload):
                continue

            target_type = ("tool_call" if obs.event_type == "tool_call" else
                           "tool_result" if obs.event_type == "tool_result" else "message")
            if policy_violation or authorization_denied:
                score = 0.95
                risk_type = "unauthorized_information_flow"
                rationale = (
                    "Observed event features indicate a policy violation or denied authorization. "
                    "This is a rule-based finding, not causal proof."
                )
            elif high_anomaly:
                score = min(float(anomaly), 1.0)
                risk_type = "anomalous_information_flow"
                rationale = (
                    "An observed anomaly score crossed the configured baseline threshold; "
                    "the score is not assumed to be a calibrated probability."
                )
            else:
                score = 0.72
                risk_type = "encoded_payload_suspicion"
                rationale = (
                    "A long base64-like or hexadecimal surface pattern was observed. "
                    "This can be benign encoding and requires contextual investigation."
                )

            findings.append(
                DetectionFinding(
                    risk_type=risk_type,
                    severity="high" if score >= 0.85 else "medium",
                    target_type=target_type,
                    target_ref=obs.target_ref or obs.event_id,
                    score=score,
                    rationale=rationale,
                    evidence_refs=[obs.event_id],
                    detector_version="rules-0.3",
                    score_semantics="uncalibrated_screening_score",
                    alternative_explanations=[
                        "Observed metadata may be incomplete or incorrectly assigned.",
                        "Encoded-looking content may be benign; contextual review is required."
                    ],
                    recommended_next_actions=["inspect_provenance", "run_matched_control", "check_authorization_policy"],
                )
            )
        return findings
