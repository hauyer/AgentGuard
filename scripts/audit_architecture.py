"""Inspect module dependencies without importing code or loading model runtimes."""

from __future__ import annotations

import argparse
import ast
import json
from graphlib import CycleError, TopologicalSorter
from importlib.util import resolve_name
from pathlib import Path
from typing import Any

LOWER_PACKAGES = frozenset({"warden", "stegopot", "fractal_bench", "torch", "transformers"})
LEGACY_MODULES = frozenset({
    "agentguard.compat.linear_probe",
    "agentguard.compat.paired_runner",
    "agentguard.compat.whitebox_activation",
    "agentguard.compat.fractal_bench",
    "agentguard.compat.stegopot",
    "agentguard.core.linear_probe",
    "agentguard.core.attribution",
    "agentguard.core.paired_runner",
    "agentguard.whitebox.activation",
    "agentguard.adapters.fractal_bench",
    "agentguard.adapters.stegopot",
})


def audit_source(source_root: Path) -> dict[str, Any]:
    """Return direct imports, legacy callers, and lower-layer boundary violations.

    Imports inside functions and literal dynamic imports are included. This static
    report does not prove the runtime reachability of conditional code paths.
    Syntax errors propagate to the caller rather than producing a clean report.
    """
    root = source_root.resolve()
    if not root.is_dir():
        raise ValueError("Source directory does not exist")
    paths = sorted(root.rglob("*.py"))
    if not paths:
        raise ValueError("Source directory contains no Python modules")
    modules = {}
    for path in paths:
        parts = list(path.relative_to(root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        modules[".".join(parts)] = path
    edges = []
    for module, path in modules.items():
        package = module if path.stem == "__init__" else module.rpartition(".")[0]
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                name = node.module or ""
                if node.level:
                    name = resolve_name("." * node.level + name, package)
                targets = [
                    f"{name}.{alias.name}" if f"{name}.{alias.name}" in modules else name
                    for alias in node.names
                ]
            elif isinstance(node, ast.Call) and node.args:
                function = node.func
                dynamic = (
                    isinstance(function, ast.Name) and function.id == "__import__"
                ) or (
                    isinstance(function, ast.Attribute) and function.attr == "import_module"
                )
                value = node.args[0]
                if dynamic and isinstance(value, ast.Constant) and isinstance(value.value, str):
                    targets = [value.value]
            for target in dict.fromkeys(targets):
                if target:
                    edges.append({
                        "source": module, "target": target,
                        "file": path.relative_to(root).as_posix(), "line": node.lineno,
                    })
    internal = [edge for edge in edges if edge["target"] in modules]
    lower = [edge for edge in edges if edge["target"].split(".")[0] in LOWER_PACKAGES]
    legacy_callers = {
        module: [edge for edge in internal if edge["target"] == module]
        for module in sorted(LEGACY_MODULES)
    }
    graph = {module: set() for module in modules}
    for edge in internal:
        graph[edge["source"]].add(edge["target"])
    cycle = []
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        cycle = list(exc.args[1])
    return {
        "module_count": len(modules), "import_count": len(edges),
        "internal_imports": internal, "lower_imports": lower,
        "legacy_callers": legacy_callers,
        "boundary_violations": [edge for edge in lower if edge["source"] not in LEGACY_MODULES],
        "dependency_cycle": cycle,
        "limitations": [
            "Static direct imports include conditional and function-local code.",
            "Computed dynamic imports and implicit package initialization require manual review.",
            "Legacy allowlisting records compatibility debt; it does not authorize production use.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    report = audit_source(arguments.root / "src")
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
    print(json.dumps({
        "module_count": report["module_count"], "import_count": report["import_count"],
        "lower_imports": report["lower_imports"],
        "boundary_violations": report["boundary_violations"],
        "dependency_cycle": report["dependency_cycle"],
    }, ensure_ascii=False, indent=2))
    return int(arguments.check and bool(report["boundary_violations"] or report["dependency_cycle"]))


if __name__ == "__main__":
    raise SystemExit(main())
