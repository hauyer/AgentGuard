"""Create a clean source ZIP with a SHA-256 sidecar."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import zipfile

EXCLUDED_PARTS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".agentguard", "dist", "build", ".tox",
}


def package(project_root: Path, output: Path) -> tuple[str, int]:
    project_root = project_root.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=8) as archive:
        for path in sorted(project_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(project_root)
            if any(part in EXCLUDED_PARTS for part in relative.parts):
                continue
            if path.suffix in {".pyc", ".pyo"} or path.name.endswith(".egg-info"):
                continue
            if path.resolve() == output:
                continue
            archive.write(path, Path("AgentGuard") / relative)
    with zipfile.ZipFile(output, "r") as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP CRC verification failed for {bad}")
        names = archive.namelist()
        forbidden = [name for name in names if any(part in EXCLUDED_PARTS for part in Path(name).parts)]
        if forbidden:
            raise RuntimeError(f"transient files unexpectedly packaged: {forbidden[:5]}")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    sidecar = output.with_suffix(output.suffix + ".sha256")
    sidecar.write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    return digest, len(names)


def main() -> None:
    parser = argparse.ArgumentParser(description="Package a clean AgentGuard source ZIP")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", default="dist/AgentGuard-0.9.0.zip")
    args = parser.parse_args()
    digest, count = package(Path(args.project_root), Path(args.output))
    print(f"Created {Path(args.output).resolve()}")
    print(f"Files: {count}")
    print(f"SHA-256: {digest}")


if __name__ == "__main__":
    main()
