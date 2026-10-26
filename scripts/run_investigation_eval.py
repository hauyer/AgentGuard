"""Execute the investigation-strategy comparison protocol (G2 multi-case).

Usage against a live stack:

    AGENTGUARD_LIVE_WARDEN_URL=http://127.0.0.1:8765 \
    python scripts/run_investigation_eval.py --out docs/research/RESULTS.md

Or with a dataset manifest:

    python scripts/run_investigation_eval.py --dataset-manifest cases.json \
        --strategies fixed_order information_value --repetitions 3

Falls back to the latest recorded real observation journal when no live kernel
is configured; if neither exists it exits with an explicit "not executed"
message and never fabricates results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.contracts.investigation_loop import InvestigationLoopBudget
from agentguard.contracts.run_config import InvestigationRunConfig
from agentguard.memory.store import ExperienceStore
from agentguard.services.evaluation_registry import EvaluationRegistry
from agentguard.services.evidence_gateway import EvidenceGateway
from agentguard.services.experiment_index import ExperimentIndexService
from agentguard.services.interaction_graph import InteractionGraphService
from agentguard.services.investigation_eval import (
    InvestigationEvaluationService,
)
from agentguard.services.investigation_loop import InvestigationLoopService
from agentguard.services.observation_workflow import ObservationRunService
from agentguard.services.risk_findings import RiskFindingService
from agentguard.services.warden_workflow_store import WardenWorkflowStore

STRATEGIES = ["fixed_order", "risk_score", "information_value", "experience", "llm"]
REPETITIONS = 2
BUDGET = InvestigationLoopBudget(
    max_actions=6, max_seconds=60.0, max_model_calls=4, no_new_evidence_rounds=3,
)


def _source_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True,
            text=True, encoding="utf-8", cwd=PROJECT_ROOT, check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def _source_tree_sha256() -> str:
    """Fingerprint the evaluated source contents, including uncommitted files."""
    digest = hashlib.sha256()
    source_roots = ("src", "scripts", "schemas", "requirements")
    source_files: set[Path] = {PROJECT_ROOT / "pyproject.toml"}
    for root_name in source_roots:
        root = PROJECT_ROOT / root_name
        if not root.exists():
            continue
        source_files.update(
            path for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
            and not any(part.endswith(".egg-info") for part in path.parts)
        )
    for path in sorted(source_files):
        if not path.is_file():
            continue
        relative_path = path.relative_to(PROJECT_ROOT).as_posix()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(PROJECT_ROOT / "docs/research/RESULTS.md"))
    parser.add_argument("--dataset-manifest", default=None,
                        help="JSON manifest with cases array")
    parser.add_argument("--base-request-ids", default=None,
                        help="Comma-separated list of base request IDs")
    parser.add_argument("--strategies", default=None,
                        help="Comma-separated list of strategies")
    parser.add_argument("--repetitions", type=int, default=REPETITIONS)
    parser.add_argument("--max-actions", type=int, default=BUDGET.max_actions)
    parser.add_argument("--max-seconds", type=float, default=BUDGET.max_seconds)
    parser.add_argument("--max-model-calls", type=int, default=BUDGET.max_model_calls)
    parser.add_argument("--partition", default="held_out_family")
    parser.add_argument("--data-dir", default=None,
                        help="Persistent data directory for workflows, artifacts, experiences, and evaluation index. "
                             "If not specified, a temporary directory is used and results are not persisted.")
    args = parser.parse_args()

    strategies = (args.strategies.split(",") if args.strategies else STRATEGIES)
    budget = InvestigationLoopBudget(
        max_actions=args.max_actions,
        max_seconds=args.max_seconds,
        max_model_calls=args.max_model_calls,
        no_new_evidence_rounds=3,
    )

    live_url = os.environ.get("AGENTGUARD_LIVE_WARDEN_URL") \
        or os.environ.get("AGENTGUARD_WARDEN_URL")

    if args.data_dir:
        data_dir = Path(args.data_dir).expanduser().resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        persisted = True
    else:
        data_dir = Path(tempfile.mkdtemp(prefix="g2-eval-"))
        persisted = False

    # Use the same filenames as the production API so that CLI-generated
    # evaluations are discoverable through the public research endpoints.
    workflows = WardenWorkflowStore(data_dir / "warden_workflows.sqlite3")
    artifacts = LocalArtifactStore(data_dir / "artifacts")
    experiences = ExperienceStore(data_dir / "experiences.sqlite3")
    eval_registry = EvaluationRegistry(workflows)

    base_request_ids: list[str] = []
    base_source = None
    manifest_sha256 = None
    source_kinds: list[str] = []
    provenance_notes: list[str] = []
    source_tree_sha256 = _source_tree_sha256()
    provenance_notes.append(
        f"AgentGuard source tree SHA-256: {source_tree_sha256}; this content hash "
        "covers source/configuration files and complements source_commit when "
        "the working tree contains uncommitted changes."
    )

    # Parse input cases
    if args.dataset_manifest:
        manifest_bytes = Path(args.dataset_manifest).read_bytes()
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        cases = manifest.get("cases", [])
        base_request_ids = [c["base_request_id"] for c in cases]
        base_source = f"dataset manifest: {manifest.get('dataset_name', 'unnamed')}"
        source_kinds = sorted({
            str(case.get("source_kind", "unspecified")) for case in cases
        }) or ["unspecified"]
        provenance_notes.append(
            f"Input manifest SHA-256: {manifest_sha256}; declared source_kind "
            f"values: {', '.join(source_kinds)}. These labels are caller-supplied "
            "and do not independently verify the source."
        )
    elif args.base_request_ids:
        base_request_ids = args.base_request_ids.split(",")
        base_source = "command line"
        source_kinds = ["unspecified"]
        provenance_notes.append(
            "Input source: command-line base_request_ids; source_kind and "
            "manifest hash are not available."
        )
    elif live_url:
        from agentguard.adapters.warden import WardenKernelClient

        try:
            kernel = WardenKernelClient(live_url)
            contracts = kernel.contracts()
            observation = ObservationRunService(
                client=kernel, workflows=workflows, artifacts=artifacts,
                experiences=experiences,
            )
            declared = observation.declared_runtime_ids()
            if contracts and declared:
                config = InvestigationRunConfig(
                    run_config_id="fixed-observation-eval-base",
                    scenario_id="release-family-g2",
                    contract_id=contracts[0],
                    model_id=declared[-1],
                    runtime_id=declared[0],
                    audit_mode="observation",
                    random_seed=7,
                    time_budget=110.0,
                    metadata={"purpose": "g2-eval-base"},
                )
                if observation.resolve(config)["status"] == "ready":
                    record = observation.execute(config)
                    if record.execution_status == "completed":
                        base_request_ids = [record.request_id]
                        base_source = "live WARDEN observation run"
                        source_kinds = ["live_observational"]
                        provenance_notes.append(
                            "Input source: this invocation's WARDEN observation; "
                            "the WARDEN request and run IDs are listed in the cases."
                        )
        except Exception as exc:  # noqa: BLE001 - fall through to journal mode
            print(f"live kernel unavailable ({type(exc).__name__}); trying journal")

    if not base_request_ids:
        print(
            "NOT EXECUTED: no live kernel, no manifest, no recorded base observation; "
            "refusing to fabricate evaluation results."
        )
        return 3

    # Validate each base request exists and classify
    valid_request_ids = []
    missing_request_ids = []
    incomplete_request_ids = []
    for rid in base_request_ids:
        entry = workflows.get(rid)
        if entry is None:
            missing_request_ids.append(rid)
            print(f"SKIP: base request '{rid}' not found (MISSING)")
        elif entry.get("status") not in {"finished", "completed"}:
            incomplete_request_ids.append(rid)
            print(f"SKIP: base request '{rid}' status={entry.get('status')} (INCOMPLETE)")
        else:
            valid_request_ids.append(rid)
            print(f"OK: base request '{rid}' valid")

    print("\nCase validation summary:")
    print(f"  Total input cases: {len(base_request_ids)}")
    print(f"  Valid cases: {len(valid_request_ids)}")
    print(f"  Missing: {len(missing_request_ids)}")
    print(f"  Incomplete: {len(incomplete_request_ids)}")

    if not valid_request_ids:
        print("NOT EXECUTED: no valid base requests found")
        return 3

    # Validate strategies
    valid_strategies = ["fixed_order", "risk_score", "information_value", "experience", "llm"]
    invalid_strategies = [s for s in strategies if s not in valid_strategies]
    if invalid_strategies:
        print(f"ERROR: invalid strategies: {invalid_strategies}")
        print(f"  Valid strategies: {valid_strategies}")
        return 2
    # Check for duplicates
    if len(strategies) != len(set(strategies)):
        print(f"ERROR: duplicate strategies in list: {strategies}")
        return 2

    loop = InvestigationLoopService(
        workflows=workflows,
        artifacts=artifacts,
        evidence_gateway=EvidenceGateway(),
        experiment_index=ExperimentIndexService(workflows, artifacts),
        risk_findings=RiskFindingService(InteractionGraphService()),
        experiences=experiences,
        observation=None,
    )
    evaluation = InvestigationEvaluationService(
        artifacts, source_commit=_source_commit(), registry=eval_registry,
    )
    report = evaluation.run_comparison(
        loop_runner=loop.run,
        base_request_ids=valid_request_ids,
        strategies=strategies,
        repetitions=args.repetitions,
        budget=budget,
        data_partition=args.partition,
        provenance_notes=provenance_notes,
    )

    lines = [
        "# 调查策略对比结果（G2，多案例）",
        "",
        f"- 执行时间：{report.executed_at.isoformat()}",
        f"- 基础案例数：{len(valid_request_ids)}（来源：{base_source}）",
        f"- 案例 ID：{', '.join(valid_request_ids)}",
        f"- 数据来源类别（manifest 声明）：{', '.join(source_kinds) or 'unspecified'}",
        f"- manifest SHA-256：`{manifest_sha256 or 'not available'}`",
        f"- source_commit：`{report.source_commit}`",
        (f"- 协议：{len(strategies)} 策略 × {args.repetitions} 重复 × {len(valid_request_ids)} 案例"
        f"（max_actions={budget.max_actions}, {budget.max_seconds}s,"
        f" model_calls={budget.max_model_calls}）；分桶 {args.partition}。"),
        "",
        "| 策略 | 完成率 | 轮数 | 首证据步数 | 有效动作比 | 冗余动作 | 证据引用数 | 降级率 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for agg in report.aggregates:
        def fmt(value):
            return "—" if value is None else str(value)

        lines.append(
            f"| {agg.strategy} | "
            f"{agg.completed_runs}/{agg.repetitions} | "
            f"{fmt(agg.mean_rounds)} | {fmt(agg.mean_steps_to_first_evidence)} | "
            f"{fmt(agg.mean_effective_action_ratio)} | "
            f"{fmt(agg.mean_redundant_actions)} | "
            f"{fmt(agg.mean_evidence_refs)} | {fmt(agg.degradation_rate)} |"
        )
    lines += [
        "",
        "## 说明与限制",
        "",
    ]
    lines.extend(f"- {note}" for note in report.limitations)
    lines += [
        "- llm 策略在无配置提供商时按合同降级到确定性策略，降级率单独列出，不计为失败。",
        ("- 风险识别类指标（Precision/Recall/F1/FPR）需要独立合法取得的真值标注，"
        "当前不计算（null）；不得以工具调用成功率替代。"),
        "- 防御效果类指标需要真实干预与独立评价，当前不计算（null）。",
        f"- 完整逐轮记录：内容寻址工件 `{report.report_artifact_id}`。",
        f"- 评价索引 ID：`eval-{report.comparison_id}`。",
    ]
    if persisted:
        lines += [
            f"- 数据目录：`{data_dir}`（持久化模式）",
            "- 评价索引与工件已保存到持久化目录，可通过 API 查询。",
        ]
    else:
        lines += [
            "- ⚠️ 临时模式：数据未持久化，服务重启后不可查询。",
            "- 使用 --data-dir 指定持久化目录以保存实验结果。",
        ]
    lines += [
        "",
    ]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out} ({len(report.runs)} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
