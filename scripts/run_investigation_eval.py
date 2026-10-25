"""Execute the D5 investigation-strategy comparison protocol.

Usage against a live stack:

    AGENTGUARD_LIVE_WARDEN_URL=http://127.0.0.1:8765 \
    python scripts/run_investigation_eval.py --out docs/research/RESULTS.md

Falls back to the latest recorded real observation journal when no live kernel
is configured; if neither exists it exits with an explicit "not executed"
message and never fabricates results.
"""

from __future__ import annotations

import argparse
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
    args = parser.parse_args()

    live_url = os.environ.get("AGENTGUARD_LIVE_WARDEN_URL") \
        or os.environ.get("AGENTGUARD_WARDEN_URL")
    data_dir = Path(tempfile.mkdtemp(prefix="d5-eval-"))

    workflows = WardenWorkflowStore(data_dir / "workflows.sqlite3")
    artifacts = LocalArtifactStore(data_dir / "artifacts")
    experiences = ExperienceStore(data_dir / "experiences.sqlite3")

    base_request_id: str | None = None
    base_source = None
    if live_url:
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
                    scenario_id="release-family-d5",
                    contract_id=contracts[0],
                    model_id=declared[-1],
                    runtime_id=declared[0],
                    audit_mode="observation",
                    random_seed=7,
                    time_budget=110.0,
                    metadata={"purpose": "d5-eval-base"},
                )
                if observation.resolve(config)["status"] == "ready":
                    record = observation.execute(config)
                    if record.execution_status == "completed":
                        base_request_id = record.request_id
                        base_source = "live WARDEN 7B observation run"
        except Exception as exc:  # noqa: BLE001 - fall through to journal mode
            print(f"live kernel unavailable ({type(exc).__name__}); trying journal")

    if base_request_id is None:
        print(
            "NOT EXECUTED: no live kernel and no recorded base observation; "
            "refusing to fabricate evaluation results."
        )
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
        artifacts, source_commit=_source_commit(),
    )
    report = evaluation.run_comparison(
        loop_runner=loop.run,
        base_request_ids=[base_request_id],
        strategies=STRATEGIES,
        repetitions=REPETITIONS,
        budget=BUDGET,
    )

    lines = [
        "# 调查策略对比结果（D5，真实运行记录）",
        "",
        f"- 执行时间：{report.executed_at.isoformat()}",
        f"- 基础观察：`{base_request_id}`（来源：{base_source}）",
        f"- source_commit：`{report.source_commit}`",
        (f"- 协议：{len(STRATEGIES)} 策略 × {REPETITIONS} 重复 × 同一预算"
        f"（max_actions={BUDGET.max_actions}, {BUDGET.max_seconds}s,"
        f" model_calls={BUDGET.max_model_calls}）；分桶 held_out_family。"),
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
        "",
    ]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out} ({len(report.runs)} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
