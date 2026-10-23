from agentguard.services.warden_diagnostics import diagnose_kernel


class Kernel:
    base_url = "http://127.0.0.1:8765"

    def __init__(self, *, api_version="v1", dispatcher=True):
        self.api_version = api_version
        self.dispatcher = dispatcher

    def health(self):
        return {"status": "ok", "version": "0.2.0", "api_version": self.api_version}

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
