"""Guard the source audit against missing direct and function-local bypasses."""

import pytest

from scripts.audit_architecture import audit_source


def test_audit_distinguishes_legacy_imports_from_production_bypass(tmp_path):
    source = tmp_path / "src" / "agentguard"
    (source / "adapters").mkdir(parents=True)
    (source / "services").mkdir()
    (source / "adapters" / "stegopot.py").write_text(
        "def offline():\n    from stegopot.bootstrap import run_file\n", encoding="utf-8",
    )
    (source / "services" / "audit.py").write_text(
        "from agentguard.adapters import stegopot\n"
        "def bypass():\n    return __import__('warden.infrastructure')\n", encoding="utf-8",
    )
    report = audit_source(tmp_path / "src")
    assert len(report["lower_imports"]) == 2
    assert report["boundary_violations"] == [{
        "source": "agentguard.services.audit", "target": "warden.infrastructure",
        "file": "agentguard/services/audit.py", "line": 3,
    }]
    assert report["legacy_callers"]["agentguard.adapters.stegopot"][0]["source"] == (
        "agentguard.services.audit"
    )


def test_audit_reports_cycles_and_rejects_unparseable_source(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        audit_source(tmp_path / "missing")
    source = tmp_path / "agentguard"
    source.mkdir()
    (source / "a.py").write_text("from . import b\n", encoding="utf-8")
    (source / "b.py").write_text("from . import a\n", encoding="utf-8")
    assert audit_source(tmp_path)["dependency_cycle"]
    (source / "broken.py").write_text("def broken(\n", encoding="utf-8")
    with pytest.raises(SyntaxError):
        audit_source(tmp_path)
