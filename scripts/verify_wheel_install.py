"""Verify the built wheel installs cleanly and matches the declared version.

Used by CI (and locally) instead of a hardcoded wheel filename:

  * exactly one wheel matching the version in ``pyproject.toml`` must exist in ``dist/``
  * it is installed into a fresh virtual environment under ``-c runtime.lock``
  * the installed version (importlib.metadata) must equal ``pyproject.toml``
  * CLI and SDK import and run from the venv, not from the repo checkout
  * package files must resolve inside the venv (no editable/source-path leak)
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PREFIX = "agentguard-"


def _repo_version() -> str:
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _find_wheel(dist: Path, version: str) -> Path:
    wheels = sorted(dist.glob(f"{EXPECTED_PREFIX}{version}-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(
            f"expected exactly one {EXPECTED_PREFIX}{version}-*.whl in {dist}, found: "
            f"{[w.name for w in wheels]}"
        )
    return wheels[0]


def main() -> int:
    version = _repo_version()
    wheel = _find_wheel(PROJECT_ROOT / "dist", version)
    print(f"wheel: {wheel.name}")
    print(f"pyproject version: {version}")
    if not wheel.name.startswith(f"{EXPECTED_PREFIX}{version}-"):
        raise SystemExit(
            f"wheel filename {wheel.name!r} does not match declared version {version!r}"
        )

    with tempfile.TemporaryDirectory(prefix="agentguard-wheel-") as tmp:
        venv = Path(tmp) / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        venv_python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--quiet",
             "-c", str(PROJECT_ROOT / "requirements/runtime.lock"), str(wheel)],
            check=True,
        )
        # Run from outside the repo so an accidental source-path import fails.
        repo_literal = str(PROJECT_ROOT).replace("\\", "\\\\")
        probe = (
            "import json, sys\n"
            "from importlib.metadata import version\n"
            "from pathlib import Path\n"
            "\n"
            "import agentguard\n"
            "import agentguard.cli\n"
            "import agentguard.sdk\n"
            "\n"
            f'repo = Path(r"{repo_literal}").resolve()\n'
            "module_paths = {\n"
            '    "agentguard": Path(agentguard.__file__).resolve(),\n'
            '    "agentguard.sdk": Path(agentguard.sdk.__file__).resolve(),\n'
            "}\n"
            "for name, path in module_paths.items():\n"
            "    if repo in path.parents:\n"
            '        raise SystemExit(f"{name} imports from the repo checkout instead of the wheel: {path}")\n'
            "    if Path(sys.prefix) not in path.parents:\n"
            '        raise SystemExit(f"{name} is not installed inside the venv: {path}")\n'
            "print(json.dumps({\n"
            '    "installed_version": version("agentguard"),\n'
            '    "sdk_client": agentguard.sdk.AgentGuardClient.__name__,\n'
            "}))\n"
        )
        completed = subprocess.run(
            [str(venv_python), "-c", probe],
            check=True, capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=tmp,
        )
        print(completed.stdout.strip())
        import json

        reported = json.loads(completed.stdout.strip().splitlines()[-1])
        if reported["installed_version"] != version:
            raise SystemExit(
                f"installed version {reported['installed_version']!r} != "
                f"pyproject version {version!r}"
            )
        cli = venv / ("Scripts/agentguard.exe" if sys.platform == "win32" else "bin/agentguard")
        subprocess.run([str(cli), "--help"], check=True, capture_output=True)
        print("cli: agentguard --help OK")
    print("wheel install verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
