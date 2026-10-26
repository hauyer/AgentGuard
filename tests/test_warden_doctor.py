from agentguard.services.warden_diagnostics import diagnose_kernel


class Kernel:
    base_url = "http://127.0.0.1:8765"

    def __init__(self, *, api_version="v1", dispatcher=True):
        self.api_version = api_version
        self.dispatcher = dispatcher

    def health(self):
        return {"status": "ok", "version": "0.2.0", "api_version": self.api_version}

    def openapi_summary(self):
        return {
            "compatible": True,
            "missing_contract_items": [],
            "path_count": 8,
            "schema_sha256": "a" * 64,
            "whitebox_workflow_api_available": "unsupported",
            "intervention_api_available": "unsupported",
            "artifact_access_available": "unsupported",
        }

    def capabilities(self):
        return [
            {"component_id": name, "available": available, "capabilities": []}
            for name, available in (
                ("agent.runtime", True),
                ("activation.backend", True),
                ("run.dispatcher", self.dispatcher),
                ("stegopot.package", True),
                ("fractal-bench.package", True),
            )
        ]

    def resources(self):
        return [{"resource_id": "probe-1", "kind": "probe", "state": "ready"}]

    def contracts(self):
        return ["contract-1"]


def test_kernel_doctor_requires_configuration_and_v1_api():
    assert diagnose_kernel(None)["status"] == "not_configured"
    report = diagnose_kernel(Kernel(api_version="v2"))
    assert report["status"] == "blocked"
    assert "WARDEN_API_VERSION_INCOMPATIBLE" in report["blockers"]
    assert "capabilities" not in report


def test_kernel_doctor_checks_scheduler_contract_and_resources():
    report = diagnose_kernel(
        Kernel(dispatcher=False), contract_id="missing", probe_id="probe-1", sae_id="sae-1",
    )
    assert report["status"] == "blocked"
    assert "COMPONENT_UNAVAILABLE:run.dispatcher" in report["blockers"]
    assert "THREAT_CONTRACT_NOT_AVAILABLE:missing" in report["blockers"]
    assert "SAE_RESOURCE_NOT_FOUND:sae-1" in report["blockers"]


def test_kernel_doctor_reports_ready_only_when_prerequisites_are_present():
    report = diagnose_kernel(Kernel(), contract_id="contract-1", probe_id="probe-1")
    assert report["status"] == "ready"
    assert report["blockers"] == []
    assert report["api_compatible"] is True
    assert report["runtime_available"] is True
    assert report["activation_available"] is True
    assert report["execution_available"] is True
    assert report["contract_source_available"] is True
    assert report["available_contracts"] == ["contract-1"]
    assert report["ready_probes"] == ["probe-1"]
    assert report["ready_saes"] == []


def test_observation_doctor_does_not_require_activation_backend():
    class BehavioralKernel(Kernel):
        def capabilities(self):
            return [
                {**item, "available": False}
                if item["component_id"] == "activation.backend" else item
                for item in super().capabilities()
            ]

    report = diagnose_kernel(BehavioralKernel(), mode="observation")
    assert report["status"] == "ready"
    assert report["mode"] == "observation"
    assert diagnose_kernel(BehavioralKernel())["status"] == "blocked"


def test_unobserved_capabilities_remain_unknown():
    class IncompleteKernel(Kernel):
        def capabilities(self):
            return []

        def contracts(self):
            return []

    report = diagnose_kernel(IncompleteKernel())
    assert report["status"] == "blocked"
    assert report["runtime_available"] == "unknown"
    assert report["activation_available"] == "unknown"
    assert report["execution_available"] == "unknown"
    assert report["contract_source_available"] == "unknown"
    assert diagnose_kernel(None)["ready_probes"] == "unknown"


def test_unverified_openapi_keeps_kernel_blocked():
    class NoOpenAPI(Kernel):
        openapi_summary = None

    report = diagnose_kernel(NoOpenAPI())
    assert report["capability_statuses"]["api_reachable"] == "available"
    assert report["capability_statuses"]["kernel_available"] == "unknown"
    assert "WARDEN_OPENAPI_NOT_VERIFIED" in report["blockers"]
    assert report["status"] == "blocked"
