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
from agentguard.services.evidence_gateway import EvidenceGateway
from agentguard.services.evaluation_registry import EvaluationRegistry
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
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True,
            text=True, encoding="utf-8", cwd=PROJECT_ROOT, check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


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
    data_dir = Path(tempfile.mkdtemp(prefix="g2-eval-"))

    workflows = WardenWorkflowStore(data_dir / "workflows.sqlite3")
    artifacts = LocalArtifactStore(data_dir / "artifacts")
    experiences = ExperienceStore(data_dir / "experiences.sqlite3")
    eval_registry = EvaluationRegistry(workflows)

    base_request_ids: list[str] = []
    base_source = None

    # Parse input cases
    if args.dataset_manifest:
        manifest = json.loads(Path(args.dataset_manifest).read_text(encoding="utf-8"))
        cases = manifest.get("cases", [])
        base_request_ids = [c["base_request_id"] for c in cases]
        base_source = f"dataset manifest: {manifest.get('dataset_name', 'unnamed')}"
    elif args.base_request_ids:
        base_request_ids = args.base_request_ids.split(",")
        base_source = "command line"
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
        except Exception as exc:  # noqa: BLE001 - fall through to journal mode
            print(f"live kernel unavailable ({type(exc).__name__}); trying journal")

    if not base_request_ids:
        print(
            "NOT EXECUTED: no live kernel, no manifest, no recorded base observation; "
            "refusing to fabricate evaluation results."
        )
        return 3

    # Validate each base request exists
    valid_request_ids = []
    for rid in base_request_ids:
        entry = workflows.get(rid)
        if entry and entry.get("status") in {"finished", "completed"}:
            valid_request_ids.append(rid)
        else:
            print(f"SKIP: base request '{rid}' not found or not finished")

    if not valid_request_ids:
        print("NOT EXECUTED: no valid base requests found")
        return 3

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
    )

    lines = [
        "# 调查策略对比结果（G2，多案例）",
        "",
        f"- 执行时间：{report.executed_at.isoformat()}",
        f"- 基础案例数：{len(valid_request_ids)}（来源：{base_source}）",
        f"- 案例 ID：{', '.join(valid_request_ids)}",
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
        f"- 评价索引 ID：`eval-{report.comparison_id}`，可通过 GET /v1/research/strategy-evaluations 查询。",
        "",
    ]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out} ({len(report.runs)} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
