from agentguard.services.research_capability import build_research_capability_report


def test_unconfigured_warden_is_unavailable_with_concrete_blocker():
    report = build_research_capability_report({"status": "not_configured", "blockers": ["WARDEN_URL_NOT_CONFIGURED"]})
    assert report.backend_status == "not_configured"
    assert len(report.capabilities) == 11
    assert {item.status for item in report.capabilities} == {"UNAVAILABLE"}
    assert all(item.blockers == ["WARDEN_URL_NOT_CONFIGURED"] for item in report.capabilities)


def test_reviewed_frozen_live_protocol_requires_runtime_token_and_artifact_contract():
    diagnostic = {"status": "ready", "api_version": "v1", "runtime_available": True,
        "research_token_configured": True, "capability_statuses": {"persistent_run_available": "available"},
        "openapi_summary": {"compatible": True, "research_request_fields_available": True,
            "artifact_content_operation_available": True, "artifact_content_media_type_compatible": True},
        "capabilities": [{"available": True, "capabilities": ["live_stego_frozen_protocol_v1", "message_block", "leace_fit_apply"]}]}
    ready = build_research_capability_report(diagnostic)
    assert next(item for item in ready.capabilities if item.capability == "leace_fit_apply").status == "SUPPORTED_AND_READY"
    diagnostic["research_token_configured"] = False
    blocked = build_research_capability_report(diagnostic)
    assert next(item for item in blocked.capabilities if item.capability == "message_block").blockers == ["WARDEN_RESEARCH_TOKEN_MISSING"]


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
    assert item.blockers == ["WARDEN_RESEARCH_OPERATION_UNREVIEWED"]
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


def test_warden_rc1_routes_are_supported_but_blocked_until_runtime_dispatcher_and_artifacts_ready():
    report = build_research_capability_report({
        "status": "ready", "kernel_version": "0.4.0rc1", "api_version": "v1",
        "research_token_configured": False, "runtime_available": False,
        "capabilities": [
            {"component_id": "agent.runtime", "available": False, "capabilities": []},
            {"component_id": "activation.backend", "available": False, "capabilities": []},
        ],
        "capability_statuses": {
            "persistent_run_available": "unsupported", "artifact_reader_available": "unsupported",
        },
        "openapi_summary": {
            "compatible": True, "service_version": "0.4.0rc1", "path_count": 9,
            "research_request_fields_available": True,
            "artifact_content_operation_available": True,
            "artifact_content_media_type_compatible": False,
            "path_methods": {
                "/v1/audit-plans": ["get", "post"],
                "/v1/runs": ["get", "post"],
                "/v1/runs/{run_id}": ["get"],
                "/v1/artifacts/{artifact_id}/content": ["get"],
            },
        },
        "blockers": [],
    })
    by_name = {item.capability: item for item in report.capabilities}
    assert by_name["research_dataset_mechanism"].status == "SUPPORTED_BUT_BLOCKED"
    assert "WARDEN_RESEARCH_STAGE_ALLOWLIST_NOT_ADVERTISED" in by_name["research_dataset_mechanism"].blockers
    assert "WARDEN_DURABLE_DISPATCHER_NOT_ADVERTISED" in by_name["persistent_run"].blockers
    assert by_name["artifact_content_read"].status == "SUPPORTED_BUT_BLOCKED"
    assert "WARDEN_ARTIFACT_OPENAPI_MEDIA_TYPE_MISMATCH" in by_name["artifact_content_read"].blockers
    assert by_name["message_block"].status == "UNSUPPORTED"
    assert by_name["research_dataset_mechanism"].source_api_version == "v1"
    assert by_name["research_dataset_mechanism"].contract_ref == "WARDEN:POST /v1/audit-plans + POST /v1/runs"

