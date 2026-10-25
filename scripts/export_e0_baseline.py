"""Export the E0 OpenAPI snapshot and the contract summary for the baseline doc."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentguard import __version__  # noqa: E402
from agentguard.api.app import create_app  # noqa: E402

FOCUS = [
    "/v1/investigations",
    "/v1/investigations/{request_id}",
    "/v1/investigations/{request_id}/interaction-graph",
    "/v1/investigations/{request_id}/timeline",
    "/v1/evidence/investigations/{request_id}",
    "/v1/experiments",
    "/v1/reports",
    "/v1/system/readiness",
    "/v1/investigations/options",
]

with tempfile.TemporaryDirectory() as tmp:
    app = create_app(data_dir=Path(tmp) / "data")
    spec = app.openapi()

version = spec["info"]["version"]
out = Path(__file__).resolve().parents[1] / "docs" / "integration" / f"openapi-{version}-e0.json"
out.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"version: {version} | total paths: {len(spec['paths'])} | snapshot: {out.name}")
for path in FOCUS:
    methods = ",".join(m.upper() for m in spec["paths"].get(path, {}) if m in {"get", "post"})
    params = []
    for method in spec["paths"].get(path, {}).values():
        for parameter in method.get("parameters", []):
            params.append(parameter.get("name"))
    print(f"  {methods:8s} {path}  params={sorted(set(p for p in params if p))}")
