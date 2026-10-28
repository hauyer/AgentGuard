"""SDK tests for the C1-C6 read-only business methods (C7)."""

import io
import json
from urllib.error import HTTPError

import pytest

from agentguard.sdk import AgentGuardAPIError, AgentGuardClient


class _Response:
    status = 200

    def __init__(self, payload):
        self.headers = {"X-Request-ID": "server-id"}
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, size=-1):
        return self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _client(payload=None):
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        if isinstance(payload, Exception):
            raise payload
        return _Response(payload or {"ok": True})

    client = AgentGuardClient("http://127.0.0.1:8000", opener=opener)
    return client, calls


def test_sdk_readiness_and_options_paths():
    client, calls = _client({"checks": []})
    client.get_readiness()
    client.get_readiness(force_refresh=True)
    client.get_investigation_options()
    assert calls[0].endswith("/v1/system/readiness")
    assert calls[1].endswith("/v1/system/readiness?force_refresh=true")
    assert calls[2].endswith("/v1/investigations/options")


def test_sdk_graph_and_timeline_paths_with_optional_params():
    client, calls = _client({"available": False})
    client.get_investigation_graph("inv/1")
    client.get_investigation_graph("inv/1", run_id="run 2")
    client.get_investigation_timeline("inv/1")
    client.get_investigation_timeline("inv/1", after_event_id=7)
    assert calls[0].endswith("/v1/investigations/inv%2F1/interaction-graph")
    assert calls[1].endswith("/v1/investigations/inv%2F1/interaction-graph?run_id=run+2")
    assert calls[2].endswith("/v1/investigations/inv%2F1/timeline")
    assert calls[3].endswith("/v1/investigations/inv%2F1/timeline?after_event_id=7")


def test_sdk_experiment_index_and_compare():
    client, calls = _client({"items": [], "total": 0})
    client.list_experiments(status="completed", scenario_id="sc", data_source="warden_workflow")
    client.get_experiment("exp-1")
    client.compare_experiments(["run-a", "run-b"])
    assert calls[0].endswith(
        "/v1/experiments?status=completed&scenario_id=sc&data_source=warden_workflow"
        "&limit=50&offset=0"
    )
    assert calls[1].endswith("/v1/experiments/exp-1")
    assert calls[2].endswith("/v1/experiments/compare")


def test_sdk_reports_index_path_and_filters():
    client, calls = _client({"items": [], "total": 0})
    client.list_reports(status="completed", scenario_id="sc-a", limit=5, offset=1)
    assert calls[0].endswith(
        "/v1/reports?status=completed&scenario_id=sc-a&limit=5&offset=1"
    )


def test_sdk_research_capability_and_study_methods_use_public_routes_and_auth_headers():
    calls = []

    def opener(request, timeout):
        calls.append(request)
        return _Response({"ok": True})

    client = AgentGuardClient(
        "http://127.0.0.1:8000", token="api-token", operator_token="operator-token", opener=opener,
    )
    client.get_research_capabilities()
    client.list_studies(limit=10)
    client.get_study("study/1")
    client.register_study({"title": "frozen"}, approval_token="approval-token")
    client.analyze_study("study/1", {"outcomes": {}}, approval_token="approval-token")
    client.list_study_analyses("study/1")
    assert [call.full_url.rsplit("/v1", 1)[-1] for call in calls] == [
        "/research/capabilities", "/studies?limit=10", "/studies/study%2F1",
        "/studies", "/studies/study%2F1/analyses", "/studies/study%2F1/analyses",
    ]
    assert calls[3].get_header("X-agentguard-token") == "api-token"
    assert calls[3].get_header("X-agentguard-operator-token") == "operator-token"
    assert calls[3].get_header("X-approval-token") == "approval-token"
    assert calls[4].get_header("X-approval-token") == "approval-token"


def test_sdk_compare_surfaces_not_comparable_responses():
    error = HTTPError(
        "http://127.0.0.1:8000/v1/experiments/compare", 200, "OK",
        {"X-Request-ID": "cmp-1"}, io.BytesIO(
            b'{"comparable": false, "status": "not_comparable", "reasons": ["RUN_REPORT_NOT_FOUND:x"]}'
        ),
    )
    client, _calls = _client(error)
    result = client.compare_experiments(["run-a", "run-x"])
    assert result["comparable"] is False
    assert result["reasons"] == ["RUN_REPORT_NOT_FOUND:x"]


def test_sdk_graph_error_keeps_request_id():
    error = HTTPError(
        "http://127.0.0.1:8000/v1/investigations/i/interaction-graph", 404, "Not Found",
        {"X-Request-ID": "graph-404"},
        io.BytesIO(b'{"detail":{"error":"WORKFLOW_NOT_FOUND"}}'),
    )
    client, _calls = _client(error)
    with pytest.raises(AgentGuardAPIError) as caught:
        client.get_investigation_graph("i")
    assert caught.value.code == "WORKFLOW_NOT_FOUND"
    assert caught.value.request_id == "graph-404"
