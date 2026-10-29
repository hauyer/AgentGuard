"""HTTP boundary security and bounded read retry behavior."""

import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import URLError

import pytest

from agentguard.adapters.warden import WardenKernelClient, WardenKernelError, warden_status

HEALTH = {"status": "ok", "version": "0.2.0", "api_version": "v1"}


def _openapi_document():
    read = {"responses": {"200": {"description": "ok"}}}

    def write(schema_name):
        return {
            "requestBody": {"content": {"application/json": {
                "schema": {"$ref": f"#/components/schemas/{schema_name}"},
            }}},
            "responses": {"200": {"description": "ok"}},
        }

    return {
        "openapi": "3.1.0", "info": {"title": "WARDEN API", "version": "0.3.0"},
        "paths": {
            "/v1/health": {"get": read},
            "/v1/capabilities": {"get": read},
            "/v1/resources": {"get": read},
            "/v1/contracts": {"get": read},
            "/v1/audit-plans": {"get": read, "post": write("AuditRequestSchema")},
            "/v1/audit-plans/{plan_id}": {"get": read},
            "/v1/runs": {"get": read, "post": write("RunRequestSchema")},
            "/v1/runs/{run_id}": {"get": read},
        },
        "components": {"schemas": {
            "AuditRequestSchema": {
                "type": "object",
                "required": ["request_id", "contract_id", "model_id", "runtime_id"],
                "properties": {
                    key: {"type": "string"}
                    for key in ("request_id", "contract_id", "model_id", "runtime_id")
                } | {
                    "mode": {"$ref": "#/components/schemas/AuditMode"},
                    "capture_targets": {
                        "type": "array", "items": {"$ref": "#/components/schemas/CaptureTargetSchema"},
                    },
                },
            },
            "AuditMode": {
                "type": "string",
                "enum": ["capture_only", "detection", "causal_audit", "defense_evaluation"],
            },
            "CaptureTargetSchema": {"type": "object", "properties": {
                "target_id": {"type": "string"}, "agent_ids": {"type": "array"},
            }},
            "RunRequestSchema": {
                "type": "object", "required": ["plan_id"],
                "properties": {"plan_id": {"type": "string"}},
            },
        }},
    }


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
        client._request("POST", "/v1/audit-plans", {"request_id": "request-1"})
    assert calls == ["POST"]


def test_openapi_summary_validates_required_routes_and_request_schemas():
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url))
        return 200, json.dumps(_openapi_document()).encode()

    summary = WardenKernelClient("https://warden.example", transport=transport).openapi_summary()
    assert summary["compatible"] is True
    assert summary["path_count"] == 8
    assert summary["schema_sha256"]
    assert summary["whitebox_workflow_api_available"] == "unsupported"
    assert summary["intervention_api_available"] == "unsupported"
    assert summary["artifact_access_available"] == "unsupported"
    assert calls == [("GET", "https://warden.example/openapi.json")]


def test_rc2_preserves_rc1_requests_and_corrects_binary_artifact_contract():
    root = Path(__file__).parent / "fixtures"
    old = json.loads((root / "warden-openapi-0.4.0rc1.json").read_text(encoding="utf-8"))
    current = json.loads((root / "warden-openapi-0.4.0rc2.json").read_text(encoding="utf-8"))
    assert current["components"]["schemas"] == old["components"]["schemas"]
    for path, methods in old["paths"].items():
        if path != "/v1/artifacts/{artifact_id}/content":
            assert current["paths"][path] == methods
    assert set(current["paths"]["/v1/artifacts/{artifact_id}/content"]["get"][
        "responses"]["200"]["content"]) == {"application/octet-stream"}


def _warden_rc1_openapi():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "warden-openapi-0.4.0rc1.json").read_text(encoding="utf-8")
    )


def test_warden_rc1_contract_reports_research_fields_and_artifact_media_mismatch():
    document = _warden_rc1_openapi()

    def transport(method, url, headers, body, timeout):
        return 200, json.dumps(document).encode()

    summary = WardenKernelClient("https://warden.example", transport=transport).openapi_summary()
    assert summary["compatible"] is True
    assert summary["path_count"] == 9
    assert summary["research_request_fields_available"] is True
    assert summary["artifact_content_operation_available"] is True
    assert summary["artifact_content_media_types"] == ["application/json"]
    assert summary["artifact_content_media_type_compatible"] is False


def test_research_plan_post_uses_bearer_token_only_after_paired_schema_check():
    document = _warden_rc1_openapi()
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, dict(headers), json.loads(body) if body else None))
        if url.endswith("/v1/health"):
            return 200, json.dumps(HEALTH).encode()
        if url.endswith("/openapi.json"):
            return 200, json.dumps(document).encode()
        return 200, json.dumps({"plan_id": "plan-research-1", "status": "planned", "blockers": []}).encode()

    client = WardenKernelClient(
        "https://warden.example", transport=transport, research_token="research-secret",
    )
    from agentguard.contracts.warden import WardenAuditRequest

    request = WardenAuditRequest(
        request_id="study-run-1", contract_id="contract-a", model_id="model-a",
        runtime_id="runtime-a", research_protocol_ref="protocol:study-1",
        study_arm_ref="study-1/treatment", study_id="study-1", study_arm_id="treatment",
    )
    result = client.create_audit_plan(request.to_warden_payload())
    post = next(item for item in calls if item[0] == "POST")
    assert result["plan_id"] == "plan-research-1"
    assert post[2]["Authorization"] == "Bearer research-secret"
    assert post[3]["research_protocol_ref"] == "protocol:study-1"
    assert post[3]["study_arm_ref"] == "study-1/treatment"
    assert "study_id" not in post[3] and "study_arm_id" not in post[3]


def test_research_plan_without_token_and_artifact_media_mismatch_never_post_or_download():
    document = _warden_rc1_openapi()
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url))
        if url.endswith("/v1/health"):
            return 200, json.dumps(HEALTH).encode()
        return 200, json.dumps(document).encode()

    client = WardenKernelClient("https://warden.example", transport=transport)
    from agentguard.contracts.warden import WardenAuditRequest

    request = WardenAuditRequest(
        request_id="study-run-1", contract_id="contract-a", model_id="model-a",
        runtime_id="runtime-a", research_protocol_ref="protocol:study-1",
        study_arm_ref="study-1/treatment",
    )
    with pytest.raises(WardenKernelError) as missing_token:
        client.create_audit_plan(request.to_warden_payload())
    assert missing_token.value.code == "WARDEN_RESEARCH_TOKEN_MISSING"
    with pytest.raises(WardenKernelError) as media_mismatch:
        client.read_public_artifact("public-summary-abc", "0" * 64)
    assert media_mismatch.value.code == "WARDEN_ARTIFACT_MEDIA_TYPE_MISMATCH"
    assert not any(method == "POST" or "/content" in url for method, url in calls)


def test_public_artifact_read_is_bounded_and_hash_verified():
    document = _warden_rc1_openapi()
    document["paths"]["/v1/artifacts/{artifact_id}/content"]["get"]["responses"]["200"]["content"] = {
        "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
    }
    payload = b"verified public summary"
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, dict(headers)))
        if url.endswith("/v1/health"):
            return 200, json.dumps(HEALTH).encode()
        if url.endswith("/openapi.json"):
            return 200, json.dumps(document).encode()
        return 200, payload

    client = WardenKernelClient(
        "https://warden.example", transport=transport, research_token="read-secret",
    )
    result = client.read_public_artifact(
        "public-summary-abc", hashlib.sha256(payload).hexdigest(), max_bytes=1024,
    )
    assert result == payload
    method, url, headers = calls[-1]
    assert method == "GET" and url.endswith("/v1/artifacts/public-summary-abc/content")
    assert headers["Accept"] == "application/octet-stream"
    assert headers["Authorization"] == "Bearer read-secret"

    with pytest.raises(WardenKernelError) as too_large:
        client.read_public_artifact("public-summary-abc", hashlib.sha256(payload).hexdigest(), max_bytes=1)
    assert too_large.value.code == "WARDEN_BAD_RESPONSE"


def test_list_audit_plans_uses_only_the_published_read_route():
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, body))
        return 200, json.dumps([{
            "plan_id": "plan-1", "status": "planned", "blockers": [],
        }]).encode()

    plans = WardenKernelClient("https://warden.example", transport=transport).list_audit_plans()
    assert plans == [{"plan_id": "plan-1", "status": "planned", "blockers": []}]
    assert calls == [("GET", "https://warden.example/v1/audit-plans", None)]


def test_missing_openapi_contract_blocks_audit_submission_before_post():
    document = _openapi_document()
    del document["paths"]["/v1/runs"]["post"]
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url))
        payload = HEALTH if url.endswith("/v1/health") else document
        return 200, json.dumps(payload).encode()

    client = WardenKernelClient("https://warden.example", transport=transport)
    from agentguard.contracts.warden import WardenAuditRequest

    request = WardenAuditRequest(
        request_id="req-contract", contract_id="contract-a",
        model_id="model-a", runtime_id="runtime-a",
    )
    with pytest.raises(WardenKernelError) as caught:
        client.create_audit_plan(request.to_warden_payload())
    assert caught.value.code == "WARDEN_API_CONTRACT_INCOMPATIBLE"
    assert calls == [
        ("GET", "https://warden.example/v1/health"),
        ("GET", "https://warden.example/openapi.json"),
    ]


def test_observation_request_uses_the_warden_capture_only_mode():
    from agentguard.contracts.warden import WardenAuditRequest

    calls = []
    payloads = [
        HEALTH,
        _openapi_document(),
        {"plan_id": "plan-observation", "status": "planned", "blockers": []},
    ]

    def transport(method, url, headers, body, timeout):
        payload = payloads.pop(0)
        if body is not None:
            calls.append(json.loads(body))
        return 200, json.dumps(payload).encode()

    client = WardenKernelClient("https://warden.example", transport=transport)
    request = WardenAuditRequest(
        request_id="req-observation", contract_id="contract-a",
        model_id="model-a", runtime_id="runtime-a", mode="observation",
    )
    client.create_audit_plan(request.to_warden_payload())
    assert calls[0]["mode"] == "capture_only"


def test_openapi_audit_mode_schema_mismatch_blocks_before_post():
    document = _openapi_document()
    document["components"]["schemas"]["AuditMode"]["enum"].append("observation")
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append(method)
        payload = HEALTH if url.endswith("/v1/health") else document
        return 200, json.dumps(payload).encode()

    client = WardenKernelClient("https://warden.example", transport=transport)
    from agentguard.contracts.warden import WardenAuditRequest

    request = WardenAuditRequest(
        request_id="req-contract", contract_id="contract-a",
        model_id="model-a", runtime_id="runtime-a",
    )
    with pytest.raises(WardenKernelError) as caught:
        client.create_audit_plan(request.to_warden_payload())
    assert caught.value.code == "WARDEN_API_CONTRACT_INCOMPATIBLE"
    assert calls == ["GET", "GET"]


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


def test_repeated_transport_reads_reuse_verified_tls_trust_configuration(monkeypatch):
    import ssl

    import agentguard.adapters.warden as boundary

    created = []
    actual_create = ssl.create_default_context

    def create_context():
        context = actual_create()
        created.append(context)
        return context

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return b"{}"

    class Opener:
        def open(self, *_args, **_kwargs):
            return Response()

    boundary._verified_tls_context.cache_clear()
    monkeypatch.setattr(boundary.ssl, "create_default_context", create_context)
    monkeypatch.setattr(boundary, "build_opener", lambda *_: Opener())
    try:
        for _ in range(3):
            assert boundary._urllib_transport("GET", "https://example.test/v1/health", {}, None, 5) == (200, b"{}")
        assert len(created) == 1
        assert created[0].verify_mode == ssl.CERT_REQUIRED
        assert created[0].check_hostname is True
    finally:
        boundary._verified_tls_context.cache_clear()
