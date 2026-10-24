"""Guard compatibility isolation and preserve the documented 0.9 import paths."""

import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from agentguard.adapters.fractal_bench import FractalBenchAdapter as LegacyFractalAdapter
from agentguard.adapters.stegopot import StegoPotAdapter as LegacyStegoPotAdapter
from agentguard.api.app import create_app
from agentguard.compat.fractal_bench import FractalBenchAdapter
from agentguard.compat.linear_probe import BinaryLinearProbe
from agentguard.compat.paired_runner import PairedExperimentRunner
from agentguard.compat.stegopot import StegoPotAdapter
from agentguard.compat.whitebox_activation import WhiteBoxActivationAdapter
from agentguard.core.linear_probe import BinaryLinearProbe as LegacyBinaryLinearProbe
from agentguard.core.paired_runner import PairedExperimentRunner as LegacyPairedExperimentRunner
from agentguard.whitebox.activation import (
    WhiteBoxActivationAdapter as LegacyWhiteBoxActivationAdapter,
)
from scripts.audit_architecture import audit_source


def test_legacy_imports_are_shims_to_the_single_compatibility_implementations():
    assert LegacyBinaryLinearProbe is BinaryLinearProbe
    assert LegacyPairedExperimentRunner is PairedExperimentRunner
    assert LegacyWhiteBoxActivationAdapter is WhiteBoxActivationAdapter
    assert LegacyFractalAdapter is FractalBenchAdapter
    assert LegacyStegoPotAdapter is StegoPotAdapter


def test_probe_routes_remain_compatible_and_marked_deprecated(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path / "data"))
    schema = client.get("/openapi.json").json()

    assert schema["paths"]["/v1/probes/train"]["post"]["deprecated"] is True
    assert schema["paths"]["/v1/probes/predict"]["post"]["deprecated"] is True
    train_description = schema["paths"]["/v1/probes/train"]["post"]["description"]
    assert "or call WARDEN" in train_description
    assert "no equivalent Probe training endpoint" in train_description


def test_normal_adapter_import_does_not_load_legacy_integrations():
    root = Path(__file__).resolve().parents[1]
    script = (
        "import sys; import agentguard.adapters.mock; "
        "assert 'agentguard.compat.fractal_bench' not in sys.modules; "
        "assert 'agentguard.compat.stegopot' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=root, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_audit_places_direct_low_level_dependencies_inside_compat_namespace():
    source_root = Path(__file__).resolve().parents[1] / "src"
    report = audit_source(source_root)

    assert report["boundary_violations"] == []
    assert report["dependency_cycle"] == []
    lower_sources = {edge["source"] for edge in report["lower_imports"]}
    assert lower_sources <= {
        "agentguard.compat.fractal_bench",
        "agentguard.compat.stegopot",
        "agentguard.compat.whitebox_activation",
    }
    assert all(not source.startswith("agentguard.api") for source in lower_sources)
