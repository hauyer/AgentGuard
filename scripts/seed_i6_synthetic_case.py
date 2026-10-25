"""Seed the isolated, clearly synthetic I6 strategy-comparison fixture.

This creates a completed WARDEN-journal-shaped record with hand-authored
messages. It does not contact WARDEN, load a model, or create real evidence.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agentguard.services.warden_workflow_store import WardenWorkflowStore

REQUEST_ID = "i6-synthetic-control-001"


def _fixture() -> tuple[dict, dict]:
    request = {
        "request_id": REQUEST_ID,
        "contract_id": "synthetic-control-contract-v1",
        "model_id": "synthetic-control-no-model",
        "runtime_id": "synthetic-control-no-runtime",
        "mode": "observation",
        "scenario_id": "i6-synthetic-control-v1",
        "metadata": {
            "source_kind": "synthetic",
            "purpose": "software-level strategy comparison only",
        },
    }
    run = {
        "run_id": "run-i6-synthetic-control-001",
        "plan_id": "plan-i6-synthetic-control-001",
        "status": "completed",
        "evidence": {
            "findings": [{
                "kind": "observational_run",
                "messages": [
                    {"message_id": "synthetic-msg-1", "sender": "reviewer-a",
                     "recipient": "coordinator", "round_index": 0},
                    {"message_id": "synthetic-msg-2", "sender": "reviewer-b",
                     "recipient": "coordinator", "round_index": 0},
                    {"message_id": "synthetic-msg-3", "sender": "reviewer-c",
                     "recipient": "coordinator", "round_index": 1},
                ],
            }],
            "artifacts": [],
        },
    }
    result = {"status": "completed", "run": run}
    return request, result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True,
                        help="An isolated data directory used only for this synthetic fixture")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    workflows = WardenWorkflowStore(data_dir / "warden_workflows.sqlite3")
    request, result = _fixture()
    current, created = workflows.begin(REQUEST_ID, request)
    if created or current["status"] not in {"finished", "completed"}:
        workflows.update(
            REQUEST_ID, "finished", context={"run": result["run"]}, result=result,
        )
    else:
        if current["result"] != result or current["request"] != request:
            raise ValueError("existing fixture differs; refusing to overwrite it")

    print(f"seeded synthetic base_request_id={REQUEST_ID}")
    print(f"data_dir={data_dir}")
    print("source_kind=synthetic; no WARDEN or model was contacted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
