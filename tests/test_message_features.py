from agentguard.core.features import extract_message_features
from agentguard.core.detector import RuleBasedDetector
from agentguard.contracts.observation import Observation


def test_message_features_are_deterministic_and_non_labeling():
    text = "Agent A sends a normal release review message."
    first = extract_message_features(text)
    second = extract_message_features(text)
    assert first == second
    assert first["message_length"] == len(text)
    assert first["token_count"] > 0
    assert first["base64_like_run"] is False
    assert first["hex_like_run"] is False


def test_detector_flags_encoded_surface_pattern_as_medium_candidate():
    encoded = "A" * 56
    features = extract_message_features(encoded)
    observation = Observation(
        experiment_id="exp-1",
        actor_id="agent-a",
        event_type="message",
        source="agent-a",
        target="agent-b",
        target_ref="trial-1:m-1",
        summary=encoded,
        features=features,
        provenance="test://message",
    )
    findings = RuleBasedDetector().detect([observation])
    assert len(findings) == 1
    assert findings[0].risk_type == "encoded_payload_suspicion"
    assert findings[0].severity == "medium"
    assert "not causal proof" not in findings[0].rationale


def test_detector_does_not_flag_ordinary_message_without_risk_features():
    observation = Observation(
        experiment_id="exp-1",
        actor_id="agent-a",
        event_type="message",
        target_ref="msg-1",
        summary="The release review is ready for the next stage.",
        features=extract_message_features("The release review is ready for the next stage."),
        provenance="test://message",
    )
    assert RuleBasedDetector().detect([observation]) == []
