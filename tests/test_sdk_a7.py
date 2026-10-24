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


def test_sdk_sends_safe_headers_and_uses_stable_paths():
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        return _Response({"items": [], "count": 0})

    client = AgentGuardClient(
        "https://agentguard.example", token="api-token", operator_token="operator-token",
        timeout=8, opener=opener,
    )
    result = client.list_investigations(status="completed", limit=10, offset=20)
    request, timeout = calls[0]
    assert result["count"] == 0
    assert request.full_url.endswith("/v1/investigations?status=completed&limit=10&offset=20")
    assert request.get_header("X-agentguard-token") == "api-token"
    assert request.get_header("X-request-id")
    assert timeout == 8


def test_sdk_quotes_identifiers_and_classifies_api_errors():
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        raise HTTPError(
            request.full_url, 403, "Forbidden", {"X-Request-ID": "denied-1"},
            io.BytesIO(b'{"detail":{"error":"OPERATOR_TOKEN_REQUIRED"}}'),
        )

    client = AgentGuardClient("http://localhost:8000", opener=opener)
    with pytest.raises(AgentGuardAPIError) as caught:
        client.get_investigation("case/with space")
    assert "/case%2Fwith%20space" in calls[0]
    assert caught.value.status_code == 403
    assert caught.value.code == "OPERATOR_TOKEN_REQUIRED"
    assert caught.value.request_id == "denied-1"


@pytest.mark.parametrize("url", [
    "file:///tmp/api", "http://user:password@localhost", "https://host/path", "http://host?token=x",
])
def test_sdk_rejects_non_origin_base_urls(url):
    with pytest.raises(ValueError):
        AgentGuardClient(url)
