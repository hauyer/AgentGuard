"""Export the current OpenAPI contract snapshot for integration reference."""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentguard.api.app import create_app

with tempfile.TemporaryDirectory() as tmp:
    app = create_app(data_dir=Path(tmp) / "data")
    spec = app.openapi()

version = spec["info"]["version"]
parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path)
args = parser.parse_args()
out = args.output or Path(__file__).resolve().parents[1] / "docs" / "integration" / f"openapi-{version}.json"
out.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
print(f"paths: {len(spec['paths'])}, version: {version}, written: {out}")
