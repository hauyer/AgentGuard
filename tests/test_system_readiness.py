"""Contract tests for layered readiness and investigation options (C3)."""

from fastapi.testclient import TestClient

from agentguard.adapters.warden import WardenKernelError
from agentguard.api.app import create_app
from agentguard.services.system_readiness import SystemReadinessService


class FakeWardenClient:
    """Duck-typed stand-in satisfying diagnose_kernel's client usage."""

    def __init__(
        self,
        *,
        health=None,
        capabilities=None,
        resources=None,
        contracts=None,
        openapi=None,
        error: str | None = None,
        openapi_error: tuple[int | None, str] | None = None,
    ) -> None:
        self.base_url = "https://warden.invalid"
        self._health = health or {"status": "ok", "version": "0.2.0", "api_version": "v1"}
        self._capabilities = capabilities if capabilities is not None else [
            {"component_id": name, "available": True, "capabilities": []}
            for name in (
                "agent.runtime", "run.dispatcher", "activation.backend",
                "stegopot.package", "fractal-bench.package",
            )
        ]
        self._resources = resources if resources is not None else [
            {"resource_id": "probe-1", "kind": "probe", "state": "ready", "version": "1"},
            {"resource_id": "sae-1", "kind": "sae", "state": "declared", "version": "1"},
        ]
        self._contracts = contracts if contracts is not None else ["contract-a", "contract-b"]
        self._openapi = openapi if openapi is not None else {
            "openapi_version": "3.1.0", "service_version": "0.3.0", "path_count": 8,
            "schema_sha256": "a" * 64, "path_methods": {},
            "missing_contract_items": [], "compatible": True,
            "whitebox_workflow_api_available": "unsupported",
            "intervention_api_available": "unsupported",
            "artifact_access_available": "unsupported",
        }
        self._error = error
        self._openapi_error = openapi_error

    def _maybe_raise(self):
        if self._error:
            raise WardenKernelError("fake transport failure", code=self._error)

    def health(self):
        self._maybe_raise()
        return self._health

    def openapi_summary(self):
        self._maybe_raise()
        if self._openapi_error:
            status_code, code = self._openapi_error
            raise WardenKernelError("fake OpenAPI failure", status_code=status_code, code=code)
        return self._openapi

    def capabilities(self):
        self._maybe_raise()
        return self._capabilities

    def resources(self, kind=None):
        self._maybe_raise()
        return self._resources if kind is None else [
            item for item in self._resources if item["kind"] == kind
        ]

    def contracts(self):
        self._maybe_raise()
        return self._contracts


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _service(client=None, *, error=None) -> tuple[SystemReadinessService, FakeClock]:
    clock = FakeClock()
    service = SystemReadinessService(
        lambda: (client, None) if client is not None or error is None else (None, error),
        ttl_seconds=30.0,
        monotonic=clock,
    )
    return service, clock


# --------------------------------------------------------------------------- #
# readiness: fully available kernel
# --------------------------------------------------------------------------- #

def test_ready_kernel_reports_layers_without_promoting_health():
    service, _ = _service(FakeWardenClient())
    response = service.readiness()
    checks = {check.name: check for check in response.checks}
    assert set(checks) == {
        "agentguard_api", "multi_host_storage", "warden_connectivity", "warden_api_compatibility",
        "contract_source", "available_contracts", "model_runtime", "probe_resources",
        "sae_resources", "audit_execution", "live_loop_execution",
        "intervention_execution", "evidence_access", "api_reachable", "kernel_available",
        "threat_contracts_available", "runtime_available", "probe_resources_available",
        "sae_resources_available", "whitebox_workflow_api_available",
        "intervention_api_available", "artifact_access_available",
    }
    assert checks["agentguard_api"].status == "available"
    assert checks["agentguard_api"].capability_version
    assert checks["multi_host_storage"].status == "unsupported"
    assert checks["multi_host_storage"].blockers == ["MULTI_HOST_STORAGE_UNSUPPORTED"]
    assert checks["multi_host_storage"].diagnostics["supported_topology"] == "single_host"
    assert checks["multi_host_storage"].diagnostics["cross_host_locking"] is False
    assert checks["warden_connectivity"].status == "available"
    assert checks["api_reachable"].status == "available"
    assert checks["kernel_available"].status == "available"
    assert checks["threat_contracts_available"].status == "available"
    assert checks["runtime_available"].status == "available"
    assert checks["probe_resources_available"].status == "available"
    assert checks["sae_resources_available"].status == "unavailable"
    assert checks["whitebox_workflow_api_available"].status == "unsupported"
    assert checks["intervention_api_available"].status == "unsupported"
    assert checks["artifact_access_available"].status == "unsupported"
    assert checks["available_contracts"].status == "available"
    assert checks["available_contracts"].diagnostics["contract_count"] == 2
    assert checks["model_runtime"].status == "available"
    assert checks["probe_resources"].status == "available"
    assert checks["sae_resources"].status == "blocked"  # declared but not ready
    assert checks["audit_execution"].status == "available"
    live_loop = checks["live_loop_execution"]
    assert live_loop.status == "blocked"
    assert "WARDEN_REMOTE_SUBMISSION_IDEMPOTENCY_UNSUPPORTED" in live_loop.blockers
    assert "WARDEN_REMOTE_STATE_NOT_DURABLE" in live_loop.blockers
    assert "WARDEN_REMOTE_AUTHORIZATION_UNVERIFIED" in live_loop.blockers
    assert "WARDEN_EVIDENCE_PROVENANCE_UNVERIFIED" in live_loop.blockers
    # Health success never implies an intervention executor exists.
    assert checks["intervention_execution"].status == "unsupported"
    assert "DEP-007" in " ".join(checks["intervention_execution"].blockers)
    assert checks["evidence_access"].status == "blocked"
    assert "DEP-004" in " ".join(checks["evidence_access"].blockers)


def test_unconfigured_warden_reports_unavailable_with_blocker():
    service, _ = _service(None)
    response = service.readiness()
    checks = {check.name: check for check in response.checks}
    assert checks["warden_connectivity"].status == "unavailable"
    assert "WARDEN_URL_NOT_CONFIGURED" in checks["warden_connectivity"].blockers
    for name in ("model_runtime", "audit_execution", "available_contracts"):
        assert checks[name].status == "unknown"
    assert checks["live_loop_execution"].status == "blocked"
    assert "WARDEN_URL_NOT_CONFIGURED" in checks["live_loop_execution"].blockers
    assert "AGENTGUARD_LOOP_OBSERVATION_DISABLED" in checks["live_loop_execution"].blockers
    assert checks["agentguard_api"].status == "available"
    assert checks["api_reachable"].status == "unavailable"
    assert checks["kernel_available"].status == "unknown"
    assert checks["whitebox_workflow_api_available"].status == "unknown"


def test_live_openapi_contract_mismatch_blocks_kernel_submission_options():
    service, _ = _service(FakeWardenClient(openapi={
        "openapi_version": "3.1.0", "service_version": "0.3.0", "path_count": 7,
        "schema_sha256": "b" * 64, "path_methods": {},
        "missing_contract_items": ["POST /v1/runs request schema RunRequestSchema"],
        "compatible": False,
        "whitebox_workflow_api_available": "unsupported",
        "intervention_api_available": "unsupported",
        "artifact_access_available": "unsupported",
    }))
    checks = {item.name: item for item in service.readiness().checks}
    assert checks["api_reachable"].status == "available"
    assert checks["kernel_available"].status == "blocked"
    options = service.investigation_options()
    assert all(not mode.submittable for mode in options.modes)
    assert "WARDEN_API_CONTRACT_INCOMPATIBLE" in options.missing_dependencies


def test_missing_live_openapi_keeps_api_reachable_but_blocks_kernel_options():
    service, _ = _service(FakeWardenClient(openapi_error=(404, "WARDEN_HTTP_ERROR")))
    checks = {item.name: item for item in service.readiness().checks}
    assert checks["api_reachable"].status == "available"
    assert checks["kernel_available"].status == "unknown"
    options = service.investigation_options()
    assert all(not mode.submittable for mode in options.modes)
    assert "WARDEN_OPENAPI_NOT_VERIFIED" in options.missing_dependencies


def test_transport_failure_and_degraded_health_are_distinguished():
    unreachable, _ = _service(FakeWardenClient(error="WARDEN_UNREACHABLE"))
    checks = {c.name: c for c in unreachable.readiness().checks}
    assert checks["warden_connectivity"].status == "unavailable"
    assert "WARDEN_UNREACHABLE" in checks["warden_connectivity"].blockers

    degraded, _ = _service(FakeWardenClient(health={
        "status": "degraded", "version": "0.2.0", "api_version": "v1",
    }))
    checks = {c.name: c for c in degraded.readiness().checks}
    assert checks["warden_connectivity"].status == "blocked"
    assert "WARDEN_HEALTH_NOT_OK" in checks["warden_connectivity"].blockers
    assert checks["model_runtime"].status == "unknown"


def test_api_version_mismatch_blocks_compatibility_and_keeps_connectivity():
    service, _ = _service(FakeWardenClient(health={
        "status": "ok", "version": "0.3.0", "api_version": "v2",
    }))
    checks = {c.name: c for c in service.readiness().checks}
    assert checks["warden_connectivity"].status == "available"
    assert checks["warden_api_compatibility"].status == "blocked"
    assert "WARDEN_API_VERSION_INCOMPATIBLE" in checks["warden_api_compatibility"].blockers
    # The probe returns early; contract availability was not measured.
    assert checks["available_contracts"].status == "unknown"


def test_missing_contracts_and_runtime_component_are_reported():
    service, _ = _service(FakeWardenClient(
        contracts=[],
        capabilities=[
            {"component_id": "agent.runtime", "available": False, "capabilities": []},
            {"component_id": "run.dispatcher", "available": True, "capabilities": []},
            {"component_id": "activation.backend", "available": True, "capabilities": []},
            {"component_id": "stegopot.package", "available": True, "capabilities": []},
            {"component_id": "fractal-bench.package", "available": True, "capabilities": []},
        ],
    ))
    checks = {c.name: c for c in service.readiness().checks}
    assert checks["available_contracts"].status == "blocked"
    assert "NO_THREAT_CONTRACTS" in checks["available_contracts"].blockers
    assert checks["model_runtime"].status == "unavailable"
    assert "COMPONENT_UNAVAILABLE:agent.runtime" in checks["model_runtime"].blockers
    assert checks["audit_execution"].status == "unavailable"


def test_declared_intervention_executor_upgrades_the_check():
    service, _ = _service(FakeWardenClient(capabilities=[
        {"component_id": "agent.runtime", "available": True, "capabilities": []},
        {"component_id": "run.dispatcher", "available": True, "capabilities": []},
        {"component_id": "activation.backend", "available": True, "capabilities": []},
        {"component_id": "stegopot.package", "available": True, "capabilities": []},
        {"component_id": "fractal-bench.package", "available": True, "capabilities": []},
        {"component_id": "intervention.executor", "available": True, "capabilities": ["block"]},
    ]))
    checks = {c.name: c for c in service.readiness().checks}
    assert checks["intervention_execution"].status == "available"
    assert checks["intervention_execution"].diagnostics["declared_by"] == "intervention.executor"


# --------------------------------------------------------------------------- #
# investigation options
# --------------------------------------------------------------------------- #

def test_options_report_per_mode_capability_and_real_resources():
    service, _ = _service(FakeWardenClient())
    options = service.investigation_options()
    assert options.available is True
    by_mode = {option.mode: option for option in options.modes}
    assert set(by_mode) == {
        "observation", "capture_only", "detection", "causal_audit", "defense_evaluation",
    }
    assert by_mode["observation"].submittable is True
    assert by_mode["causal_audit"].submittable is True
    assert by_mode["defense_evaluation"].submittable is True
    assert "approval token" in by_mode["defense_evaluation"].note
    assert options.threat_contracts == ["contract-a", "contract-b"]
    assert [p.resource_id for p in options.probe_resources] == ["probe-1"]
    assert options.probe_resources[0].state == "ready"
    assert [s.state for s in options.sae_resources] == ["declared"]
    assert options.capture_targets.activation_capture_available is True
    assert "agent_ids" in options.capture_targets.accepted_fields
    # Without capability metadata the runtime list stays empty and says so.
    assert options.confirmed_runtime_ids == []
    assert any("does not publish runtime identifiers" in note for note in options.limitations)


def test_options_surface_runtime_ids_declared_by_kernel_metadata():
    service, _ = _service(FakeWardenClient(capabilities=[
        {"component_id": "agent.runtime", "available": True,
         "capabilities": [], "metadata": {
             "runtime_id": "stegopot.v1",
             "model_ids": ["qwen2.5:7b-instruct-q4_K_M", "qwen2.5:3b-instruct-q4_K_M"],
         }},
        {"component_id": "run.dispatcher", "available": True, "capabilities": []},
        {"component_id": "activation.backend", "available": True, "capabilities": []},
        {"component_id": "stegopot.package", "available": True, "capabilities": []},
        {"component_id": "fractal-bench.package", "available": True, "capabilities": []},
    ]))
    options = service.investigation_options()
    assert options.confirmed_runtime_ids == [
        "stegopot.v1", "qwen2.5:7b-instruct-q4_K_M", "qwen2.5:3b-instruct-q4_K_M",
    ]
    assert any("capability metadata" in note for note in options.limitations)


def test_options_hide_runtime_ids_when_runtime_unavailable():
    service, _ = _service(FakeWardenClient(capabilities=[
        {"component_id": "agent.runtime", "available": False,
         "capabilities": [], "metadata": {"runtime_id": "stegopot.v1"}},
        {"component_id": "run.dispatcher", "available": True, "capabilities": []},
        {"component_id": "activation.backend", "available": True, "capabilities": []},
        {"component_id": "stegopot.package", "available": True, "capabilities": []},
        {"component_id": "fractal-bench.package", "available": True, "capabilities": []},
    ]))
    options = service.investigation_options()
    assert options.confirmed_runtime_ids == []


def test_options_without_activation_backend_block_only_non_observation_modes():
    service, _ = _service(FakeWardenClient(capabilities=[
        {"component_id": "agent.runtime", "available": True, "capabilities": []},
        {"component_id": "run.dispatcher", "available": True, "capabilities": []},
        {"component_id": "activation.backend", "available": False, "capabilities": []},
        {"component_id": "stegopot.package", "available": True, "capabilities": []},
        {"component_id": "fractal-bench.package", "available": True, "capabilities": []},
    ]))
    options = service.investigation_options()
    by_mode = {option.mode: option for option in options.modes}
    assert by_mode["observation"].submittable is True
    for mode in ("capture_only", "detection", "causal_audit", "defense_evaluation"):
        assert by_mode[mode].run_execution == "unavailable"
        assert by_mode[mode].submittable is False
        assert "COMPONENT_UNAVAILABLE:activation.backend" in by_mode[mode].blockers
    assert options.capture_targets.activation_capture_available is False


def test_options_unavailable_when_warden_not_configured():
    service, _ = _service(None)
    options = service.investigation_options()
    assert options.available is False
    assert options.error_code == "WARDEN_URL_NOT_CONFIGURED"
    assert all(option.submittable is False for option in options.modes)
    assert "WARDEN_URL_NOT_CONFIGURED" in options.missing_dependencies
    assert options.threat_contracts == []


# --------------------------------------------------------------------------- #
# caching
# --------------------------------------------------------------------------- #

def test_readiness_uses_short_ttl_cache_with_provenance():
    probe_client = FakeWardenClient()
    clock = FakeClock()
    calls = {"health": 0}
    original_health = probe_client.health

    def counting_health():
        calls["health"] += 1
        return original_health()

    probe_client.health = counting_health
    service = SystemReadinessService(
        lambda: (probe_client, None), ttl_seconds=30.0, monotonic=clock,
    )
    first = service.readiness()
    assert first.cache.source == "live"
    clock.now += 10.0
    second = service.readiness()
    assert second.cache.source == "cache"
    assert second.cache.is_stale is False
    assert second.cache.age_seconds == 10.0
    clock.now += 25.0  # beyond TTL
    third = service.readiness()
    assert third.cache.source == "live"
    assert calls["health"] == 2  # one probe per live refresh only
    forced = service.readiness(force_refresh=True)
    assert forced.cache.source == "live"
    assert calls["health"] == 3


# --------------------------------------------------------------------------- #
# API surface
# --------------------------------------------------------------------------- #

def test_api_serves_readiness_and_options_routes(tmp_path):
    app = create_app(data_dir=tmp_path / "data")
    app.state.system_readiness, _clock = _service(FakeWardenClient())
    with TestClient(app) as client:
        readiness = client.get("/v1/system/readiness")
        options = client.get("/v1/investigations/options")
        detail_conflict = client.get("/v1/investigations/options")  # not swallowed by {request_id}
        schema = client.get("/openapi.json").json()
    assert readiness.status_code == 200
    body = readiness.json()
    assert body["schema_version"] == "1.0"
    assert {check["name"] for check in body["checks"]} >= {"model_runtime", "intervention_execution"}
    assert readiness.headers.get("X-Request-ID")
    assert options.status_code == 200
    assert options.json()["available"] is True
    assert detail_conflict.status_code == 200
    assert "get" in schema["paths"]["/v1/system/readiness"]
    assert "get" in schema["paths"]["/v1/investigations/options"]
    # A real lookup of request_id="options" must not 404-conflict with the route above.
    with TestClient(app) as client:
        missing = client.get("/v1/investigations/definitely-not-a-workflow")
    assert missing.status_code == 404
