from agentguard.services.research_capability import build_research_capability_report


def test_unconfigured_warden_is_unavailable_with_concrete_blocker():
    report = build_research_capability_report({"status": "not_configured", "blockers": ["WARDEN_URL_NOT_CONFIGURED"]})
    assert report.backend_status == "not_configured"
    assert len(report.capabilities) == 11
    assert {item.status for item in report.capabilities} == {"UNAVAILABLE"}
    assert all(item.blockers == ["WARDEN_URL_NOT_CONFIGURED"] for item in report.capabilities)


def test_compatible_warden_without_research_routes_is_explicitly_unsupported():
    report = build_research_capability_report({
        "status": "ready",
        "kernel_version": "0.3.0",
        "api_version": "v1",
        "capabilities": [{"component_id": "agent.runtime", "capabilities": []}],
        "openapi_summary": {"compatible": True, "service_version": "0.3.0", "path_count": 10},
        "blockers": [],
    })
    assert report.backend_status == "available"
    assert report.source_version == "0.3.0"
    assert {item.status for item in report.capabilities} == {"UNSUPPORTED"}
    assert all(item.blockers == ["WARDEN_RESEARCH_CAPABILITY_NOT_DECLARED"] for item in report.capabilities)
    assert all(item.advertised_by_warden is False for item in report.capabilities)


def test_advertised_capability_without_reviewed_rest_operation_stays_blocked():
    report = build_research_capability_report({
        "status": "ready",
        "kernel_version": "0.4.0-candidate",
        "api_version": "v1",
        "capabilities": [{"component_id": "research", "capabilities": ["message_block"]}],
        "openapi_summary": {"compatible": True, "service_version": "0.4.0-candidate"},
        "blockers": [],
    })
    item = next(item for item in report.capabilities if item.capability == "message_block")
    assert item.status == "SUPPORTED_BUT_BLOCKED"
    assert item.blockers == ["WARDEN_RESEARCH_OPERATION_CONTRACT_UNREVIEWED"]
    assert item.advertised_by_warden is True


def test_unavailable_kernel_does_not_leak_raw_exception_or_host():
    report = build_research_capability_report({
        "status": "unavailable",
        "base_url": "https://internal.example/model/path",
        "reason": "secret traceback",
        "blockers": ["WARDEN_TIMEOUT"],
    }).model_dump(mode="json")
    serialized = str(report)
    assert "internal.example" not in serialized
    assert "secret traceback" not in serialized
    assert all(item["status"] == "UNAVAILABLE" for item in report["capabilities"])

