"""HTTP boundary security and bounded read retry behavior."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.error import URLError

import pytest

from agentguard.adapters.warden import WardenKernelClient, WardenKernelError, warden_status

HEALTH = {"status": "ok", "version": "0.2.0", "api_version": "v1"}


@pytest.mark.parametrize("url", [
    "https://user:credential-sentinel@warden.example",
    "https://warden.example/?token=credential-sentinel",
    "https://warden.example/#credential-sentinel",
    "https://warden.example:0", "https://warden.example:bad",
    "http://127.0.0.1\\@other.example", "http://127.0.0.1/\r\n",
    "https://warden.example/%2e%2e/private", "https://warden.example/?",
])
def test_kernel_url_rejects_ambiguous_or_credential_bearing_values(url):
    with pytest.raises(ValueError):
        WardenKernelClient(url)


def test_misconfiguration_report_never_echoes_credentials(monkeypatch):
    monkeypatch.setenv("AGENTGUARD_WARDEN_URL", "https://user:credential-sentinel@warden.example")
    report = warden_status()
    assert report["status"] == "misconfigured"
    assert "credential-sentinel" not in json.dumps(report)


@pytest.mark.parametrize("method,code", [("GET", 302), ("POST", 307)])
def test_actual_http_redirect_never_follows_location(method, code):
    paths = []

    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(code)
            self.send_header("Location", "/redirect-target?credential-sentinel")
            self.end_headers()

        do_POST = do_GET

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    client = WardenKernelClient(f"http://127.0.0.1:{server.server_port}")
    try:
        with pytest.raises(WardenKernelError) as caught:
            client._request(method, "/v1/health")
        assert caught.value.code == "WARDEN_REDIRECT_REJECTED"
        assert paths == ["/v1/health"]
        assert "credential-sentinel" not in str(caught.value)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_only_get_retries_transient_network_and_http_errors():
    calls = []
    sleeps = []

    def flaky(method, url, headers, body, timeout):
        calls.append(method)
        if len(calls) == 1:
            raise WardenKernelError("temporary timeout", code="WARDEN_TIMEOUT")
        if len(calls) == 2:
            return 503, b"not JSON and not needed for retry"
        return 200, json.dumps(HEALTH).encode()

    client = WardenKernelClient(
        "https://warden.example", transport=flaky, read_retries=2, sleep=sleeps.append,
    )
    assert client.health() == HEALTH
    assert calls == ["GET"] * 3
    assert sleeps == [0.1, 0.2]
    calls.clear()
    with pytest.raises(WardenKernelError):
        client.create_audit_plan({"request_id": "request-1"})
    assert calls == ["POST"]


def test_timeout_wrapped_in_urlerror_is_classified_and_sanitized(monkeypatch):
    import agentguard.adapters.warden as boundary

    class TimeoutOpener:
        def open(self, request, timeout):
            raise URLError(TimeoutError("credential-sentinel"))

    monkeypatch.setattr(boundary, "build_opener", lambda *_: TimeoutOpener())
    with pytest.raises(WardenKernelError) as caught:
        WardenKernelClient("https://warden.example").health()
    assert caught.value.code == "WARDEN_TIMEOUT"
    assert "credential-sentinel" not in str(caught.value)


@pytest.mark.parametrize("raw", [
    b'{"status":"ok","status":"failed"}', b'{"score":NaN}',
    b"", b"not JSON", b"x" * (2 * 1024 * 1024 + 1),
], ids=["duplicate-key", "nonfinite", "empty", "nonjson", "oversized"])
def test_invalid_or_oversized_response_is_not_retried(raw):
    calls = []

    def transport(*args):
        calls.append(args)
        return 200, raw

    with pytest.raises(WardenKernelError) as caught:
        WardenKernelClient("https://warden.example", transport=transport, read_retries=3).health()
    assert caught.value.code == "WARDEN_BAD_RESPONSE"
    assert len(calls) == 1


@pytest.mark.parametrize("endpoint,payload", [
    ("capabilities", [{"component_id": "runtime", "available": "true"}]),
    ("capabilities", [{"component_id": "runtime", "available": True}] * 2),
    ("resources", [{"resource_id": "probe-1", "kind": "probe", "state": "unknown"}]),
    ("contracts", ["contract-1", "contract-1"]),
    ("health", {"status": "ok"}),
])
def test_response_field_validation_rejects_ambiguous_capability_data(endpoint, payload):
    client = WardenKernelClient(
        "https://warden.example", transport=lambda *args: (200, json.dumps(payload).encode()),
    )
    with pytest.raises(WardenKernelError) as caught:
        getattr(client, endpoint)()
    assert caught.value.code == "WARDEN_BAD_RESPONSE"


@pytest.mark.parametrize("health,error_code", [
    ({**HEALTH, "api_version": "v2"}, "WARDEN_API_INCOMPATIBLE"),
    ({**HEALTH, "status": "degraded"}, "WARDEN_HEALTH_NOT_OK"),
])
def test_unready_api_never_receives_mutating_request(health, error_code):
    methods = []

    def transport(method, *args):
        methods.append(method)
        return 200, json.dumps(health).encode()

    client = WardenKernelClient("https://warden.example", transport=transport)
    with pytest.raises(WardenKernelError) as caught:
        client.create_audit_plan({})
    assert caught.value.code == error_code
    assert methods == ["GET"]
