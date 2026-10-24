import importlib.util
import zipfile
from pathlib import Path

from agentguard import __version__

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "package_release.py"
_SPEC = importlib.util.spec_from_file_location("agentguard_package_release", _SCRIPT)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
package = _MODULE.package


def test_release_packager_includes_design_docs_and_excludes_transient_files(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    # Create a temporary transient directory to assert the packaging filter.
    transient = project_root / ".agentguard" / "packager-test"
    transient.parent.mkdir(parents=True, exist_ok=True)
    transient.write_text("must not ship", encoding="utf-8")
    try:
        output = tmp_path / f"AgentGuard-{__version__}.zip"
        digest, count = package(project_root, output)
        assert len(digest) == 64
        assert count > 50
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            assert "AgentGuard/docs/AgentGuard_最终版总体设计与开发规范.md" in names
            assert "AgentGuard/docs/IMPLEMENTATION_STATUS.md" in names
            assert "AgentGuard/src/agentguard/core/linear_probe.py" in names
            assert "AgentGuard/src/agentguard/compat/linear_probe.py" in names
            assert "AgentGuard/src/agentguard/compat/paired_runner.py" in names
            assert "AgentGuard/src/agentguard/compat/stegopot.py" in names
            assert "AgentGuard/src/agentguard/compat/fractal_bench.py" in names
            assert "AgentGuard/src/agentguard/compat/whitebox_activation.py" in names
            assert "AgentGuard/docs/RESEARCH_WORKFLOW.md" in names
            assert "AgentGuard/docs/WHITEBOX_INSTRUMENTATION.md" in names
            assert "AgentGuard/src/agentguard/whitebox/activation.py" in names
            assert "AgentGuard/src/agentguard/core/experience_bandit.py" in names
            assert "AgentGuard/src/agentguard/contracts/experience_bandit.py" in names
            assert "AgentGuard/docs/EXPERIENCE_BANDIT.md" in names
            assert "AgentGuard/schemas/experience-bandit-recommendation-report.schema.json" in names
            assert "AgentGuard/schemas/activation-patch-spec.schema.json" in names
            assert not any("packager-test" in name or ".agentguard/" in name for name in names)
            assert archive.testzip() is None
    finally:
        transient.unlink(missing_ok=True)
