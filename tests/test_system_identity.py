import hashlib
import json

from fastapi.testclient import TestClient

from agentguard import __version__
from agentguard.api.app import create_app


def test_identity_endpoint_is_public_and_hashes_runtime_openapi(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTGUARD_API_TOKEN", "do-not-disclose")
    monkeypatch.delenv("AGENTGUARD_BUILD_COMMIT", raising=False)
    app = create_app(data_dir=tmp_path / "data")
    with TestClient(app) as client:
        identity_response = client.get("/v1/system/identity")
        openapi = client.get("/openapi.json").json()

    assert identity_response.status_code == 200
    identity = identity_response.json()
    canonical = json.dumps(
        openapi, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    assert identity["software_version"] == __version__
    assert identity["api_contract_version"] == __version__
    assert identity["build_commit"] == "unknown"
    assert identity["build_commit_source"] == "unknown"
    assert identity["runtime_identity_status"] == "unknown"
    assert identity["openapi_sha256"] == hashlib.sha256(canonical).hexdigest()
    assert identity["capabilities"]["live_warden_loop"] == "blocked"
    assert "do-not-disclose" not in identity_response.text
    assert "/v1/system/identity" in openapi["paths"]


def test_identity_reports_only_a_valid_build_commit_declaration(tmp_path, monkeypatch):
    commit = "a" * 40
    monkeypatch.setenv("AGENTGUARD_BUILD_COMMIT", commit)
    app = create_app(data_dir=tmp_path / "data")
    with TestClient(app) as client:
        declared = client.get("/v1/system/identity").json()
    assert declared["build_commit"] == commit
    assert declared["build_commit_source"] == "AGENTGUARD_BUILD_COMMIT"
    assert declared["runtime_identity_status"] == "declared"

    monkeypatch.setenv("AGENTGUARD_BUILD_COMMIT", "not-a-commit")
    app = create_app(data_dir=tmp_path / "data2")
    with TestClient(app) as client:
        invalid = client.get("/v1/system/identity").json()
    assert invalid["build_commit"] == "unknown"
    assert invalid["runtime_identity_status"] == "unknown"
