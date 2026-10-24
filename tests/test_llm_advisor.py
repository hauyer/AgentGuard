import json

from agentguard.agents.llm_advisor import LLMInvestigationAdvisor
from agentguard.agents.model_client import ModelClientError, OpenAICompatibleJSONClient
from agentguard.contracts.observation import DetectionFinding


def finding(fid, score):
    return DetectionFinding(
        finding_id=fid,
        risk_type="unauthorized_information_flow",
        severity="high",
        target_type="message",
        target_ref=f"target-{fid}",
        score=score,
        rationale="Observed policy violation candidate; causal mechanism remains unverified.",
        evidence_refs=[f"event-{fid}"],
    )


def test_llm_advisor_selects_only_an_existing_finding_and_sends_no_tools():
    captured = {}

    def transport(url, headers, body, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["request"] = json.loads(body)
        candidate_id = captured["request"]["messages"][1]["content"]
        payload = json.loads(candidate_id)
        selected = payload["candidate_findings"][1]["finding_id"]
        response = {
            "choices": [{"message": {"content": json.dumps({
                "selected_finding_id": selected,
                "rationale": "The second candidate has a clearer evidence path.",
                "confidence": 0.77,
            })}}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 15, "total_tokens": 45},
        }
        return json.dumps(response).encode()

    client = OpenAICompatibleJSONClient(
        base_url="https://api.deepseek.com/v1", api_key="secret-test-key",
        model="deepseek-chat", transport=transport,
    )
    advisor = LLMInvestigationAdvisor(client)
    selected = advisor.select_finding([finding("f-1", 0.99), finding("f-2", 0.6)])
    assert selected == "f-2"
    assert advisor.last_decision["status"] == "advice_received"
    assert advisor.last_decision["confidence"] == 0.77
    assert "tools" not in captured["request"]
    assert captured["headers"]["Authorization"] == "Bearer secret-test-key"
    assert "secret-test-key" not in str(advisor.last_decision)


def test_llm_advisor_rejects_fabricated_ids_and_deterministic_fallback_is_available():
    def transport(url, headers, body, timeout):
        return json.dumps({"choices": [{"message": {"content": json.dumps({
            "selected_finding_id": "not-a-candidate", "rationale": "fabricated", "confidence": 1.0,
        })}}]}).encode()

    advisor = LLMInvestigationAdvisor(OpenAICompatibleJSONClient(
        base_url="https://api.deepseek.com/v1", api_key="secret", model="deepseek-chat", transport=transport,
    ))
    assert advisor.select_finding([finding("real-id", 0.9)]) is None
    assert advisor.last_decision["status"] == "invalid_candidate_rejected"


def test_client_sanitizes_transport_errors_and_requires_https():
    def fail(url, headers, body, timeout):
        raise RuntimeError("sensitive provider response and key")

    client = OpenAICompatibleJSONClient(
        base_url="https://api.deepseek.com/v1", api_key="top-secret", model="deepseek-chat", transport=fail,
    )
    try:
        client.complete_json(system_prompt="test", payload={})
    except ModelClientError as exc:
        assert "top-secret" not in str(exc)
        assert "sensitive provider response" not in str(exc)
    else:
        raise AssertionError("expected sanitized model client error")

    try:
        OpenAICompatibleJSONClient(base_url="http://example.com/v1", api_key="x", model="m")
    except ValueError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("non-loopback HTTP should be rejected")


def test_llm_advice_schema_rejects_extra_fields_and_respects_output_budget():
    captured = {}

    def transport(url, headers, body, timeout):
        captured["request"] = json.loads(body)
        payload = json.loads(captured["request"]["messages"][1]["content"])
        return json.dumps({
            "choices": [{"message": {"content": json.dumps({
                "selected_finding_id": payload["candidate_findings"][0]["finding_id"],
                "rationale": "This is a bounded advisory selection.",
                "confidence": 0.5,
                "execute_action": True,
            })}}],
            "usage": {"prompt_tokens": 17, "completion_tokens": 7, "total_tokens": 24},
        }).encode()

    advisor = LLMInvestigationAdvisor(OpenAICompatibleJSONClient(
        base_url="https://api.deepseek.com/v1", api_key="secret", model="test", transport=transport,
    ))
    assert advisor.select_finding(
        [finding("allowed-a", 0.8), finding("allowed-b", 0.7)],
        max_tokens=11,
        max_candidates=1,
    ) is None
    assert advisor.last_decision["status"] == "invalid_response_rejected"
    assert captured["request"]["max_tokens"] == 11
    assert "tools" not in captured["request"]
