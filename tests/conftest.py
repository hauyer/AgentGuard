import pytest


@pytest.fixture
def warden_openapi_document():
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
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/CaptureTargetSchema"},
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
