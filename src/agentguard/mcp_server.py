"""Optional MCP server exposing bounded research tools, never arbitrary execution.

Install with ``pip install -e '.[mcp]'``. The bundled server intentionally uses the
synthetic MockBackend until a separately reviewed runtime configuration is supplied.
"""
from __future__ import annotations
import json
import os
from pathlib import Path

from agentguard.adapters.mock import MockBackend
from agentguard.contracts.experience import ExperienceRecord
from agentguard.contracts.experiment import ExperimentRequest
from agentguard.core.orchestrator import AgentGuardEngine
from agentguard.core.paired_runner import PairedExperimentRunner, PairedExecutionUnsupported
from agentguard.contracts.intervention import InterventionRequest
from agentguard.memory.store import ExperienceStore
from agentguard.artifacts.local_store import LocalArtifactStore
from agentguard.contracts.evidence import EvidenceRecord
from agentguard.core.policy import InterventionPolicyEngine


def create_mcp_server():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("MCP support is optional. Install with: pip install -e '.[mcp]'") from exc

    mcp = FastMCP("AgentGuard Research Tools")
    backend = MockBackend()
    engine = AgentGuardEngine(backend, backend, backend)
    reports = {}
    data_dir = Path(os.environ.get("AGENTGUARD_DATA_DIR", ".agentguard"))
    memory = ExperienceStore(data_dir / "experiences.sqlite3")
    artifacts = LocalArtifactStore(data_dir / "artifacts")
    policy_engine = InterventionPolicyEngine()

    @mcp.tool()
    def list_scenarios() -> str:
        """List scenario IDs and public descriptions from the configured scenario provider."""
        return json.dumps([s.model_dump(mode="json") for s in engine.scenarios.list_scenarios()], ensure_ascii=False)

    @mcp.tool()
    def get_capabilities() -> str:
        """Return the runtime's explicit capability manifest and limitations."""
        return engine.runtime.capabilities().model_dump_json(indent=2)

    @mcp.tool()
    def start_experiment(scenario_id: str, seed: int = 7, max_interventions: int = 1) -> str:
        """Run an isolated synthetic experiment; auto-intervention is disabled by default."""
        request = ExperimentRequest(
            scenario_id=scenario_id, runtime_backend="mock", seed=seed,
            max_interventions=max_interventions, auto_intervene=False,
        )
        report = engine.run(request)
        reports[report.run_id] = report
        return report.model_dump_json(indent=2)

    @mcp.tool()
    def get_run_report(run_id: str) -> str:
        """Retrieve a report produced during this MCP server process."""
        report = reports.get(run_id)
        if report is None:
            return json.dumps({"error": "RUN_NOT_FOUND", "run_id": run_id})
        return report.model_dump_json(indent=2)

    @mcp.tool()
    def get_findings(run_id: str) -> str:
        """Return findings and their evidence references for a known run."""
        report = reports.get(run_id)
        if report is None:
            return json.dumps({"error": "RUN_NOT_FOUND", "run_id": run_id})
        return json.dumps([item.model_dump(mode="json") for item in report.findings], ensure_ascii=False)

    @mcp.tool()
    def run_paired_synthetic_experiment(run_id: str, authorization_ref: str) -> str:
        """Execute a matched control/treatment/negative-control workflow on a known synthetic run."""
        report = reports.get(run_id)
        if report is None:
            return json.dumps({"error": "RUN_NOT_FOUND", "run_id": run_id})
        if report.request_snapshot is None or not report.experiment_designs:
            return json.dumps({"error": "PAIRED_DESIGN_NOT_FOUND", "run_id": run_id})
        design = report.experiment_designs[0]
        action = next((item for item in report.investigation_actions if item.hypothesis_id == design.hypothesis_id), None)
        if action is None:
            return json.dumps({"error": "INVESTIGATION_ACTION_NOT_FOUND", "run_id": run_id})
        scenario = engine.scenarios.get_scenario(report.scenario_id)
        budget = report.budget_usage.max_interventions if report.budget_usage else 0
        used = report.budget_usage.intervention_attempts if report.budget_usage else 0
        if used + design.repetitions > budget:
            return json.dumps({"error": "BUDGET_EXCEEDED", "message": f"paired design needs {design.repetitions} intervention attempts but only {max(0, budget-used)} remain"})
        template = InterventionRequest(
            experiment_id=report.experiment_id, intervention_id=f"mcp-paired-{design.design_id}",
            target_type=action.target_type, target_ref=action.target_ref, operation=action.operation,
            parameters={"scope": "synthetic_demo"}, authorization_ref=authorization_ref,
        )
        decision = policy_engine.evaluate(
            scenario, template, engine.runtime.capabilities(), max_interventions=budget,
            interventions_already_attempted=used, approval_granted=False,
        )
        if not decision.allowed:
            return json.dumps({"error": decision.error_code, "message": decision.reason})
        try:
            execution = PairedExperimentRunner(engine.runtime).run(
                run_id=report.run_id, scenario=scenario, base_request=report.request_snapshot,
                design=design, intervention_template=template,
            )
        except PairedExecutionUnsupported as exc:
            return json.dumps({"error": "PAIRED_REPLAY_UNAVAILABLE", "message": str(exc)})
        artifact = artifacts.put(
            execution.model_dump_json(indent=2).encode("utf-8"),
            name=f"paired-experiment-{execution.design_id}.json",
            description="MCP-triggered synthetic paired experiment report",
            tags=["paired-experiment", execution.evidence_mode, execution.status],
            metadata={"run_id": report.run_id, "design_id": execution.design_id,
                      "completed_pairs": execution.completed_pairs},
            media_type="application/json",
        )
        execution.artifact_id = artifact.artifact_ref.artifact_id
        report.paired_experiments.append(execution)
        for pair in execution.pairs:
            if pair.intervention_request is not None:
                report.intervention_requests.append(pair.intervention_request)
            if pair.intervention_result is not None:
                report.intervention_history.append(pair.intervention_result)
                report.intervention_result = pair.intervention_result
                report.selected_intervention = pair.intervention_request
        if report.budget_usage is not None:
            attempts = sum(pair.intervention_result is not None for pair in execution.pairs)
            report.budget_usage.intervention_attempts += attempts
            report.budget_usage.intervention_requests += attempts
        report.causal_claim_status = "simulated_only"
        report.evidence.append(EvidenceRecord(
            kind="evaluation", grade="synthetic", source="agentguard.mcp.paired_runner",
            summary=f"Synthetic paired execution completed {execution.completed_pairs}/{execution.requested_pairs} pairs.",
            artifact_refs=[artifact.artifact_ref.artifact_id], run_id=report.run_id,
            experiment_id=report.experiment_id, scenario_id=report.scenario_id,
            source_version="mcp-paired-0.4", metadata={"design_id": execution.design_id, "status": execution.status},
        ))
        return execution.model_dump_json(indent=2)

    @mcp.tool()
    def propose_defense(run_id: str) -> str:
        """Generate a defense preview only; this tool does not execute an intervention."""
        report = reports.get(run_id)
        if report is None:
            return json.dumps({"error": "RUN_NOT_FOUND", "run_id": run_id})
        return engine.defense_service.preview(report).model_dump_json(indent=2)

    @mcp.tool()
    def search_verified_experiences(query: str = "", scenario_id: str | None = None, risk_type: str | None = None) -> str:
        """Search versioned verified memories; returned recommendations never execute automatically."""
        items = memory.search(query, scenario_id=scenario_id, risk_type=risk_type, verified_only=True, limit=50)
        return json.dumps([item.model_dump(mode="json") for item in items], ensure_ascii=False)

    @mcp.tool()
    def record_unverified_experience(
        scenario_id: str, risk_type: str, hypothesis_summary: str,
        action_type: str, action_summary: str, outcome: str,
    ) -> str:
        """Record an explicitly unverified research note; it cannot become an automatic recommendation."""
        record = ExperienceRecord(
            scenario_id=scenario_id, risk_type=risk_type,
            hypothesis_summary=hypothesis_summary, action_type=action_type,
            action_summary=action_summary, outcome=outcome, evidence_grade="observational",
            verification_status="unverified", allow_auto_recommendation=False,
        )
        memory.add(record)
        return record.model_dump_json(indent=2)

    return mcp


def main() -> None:
    server = create_mcp_server()
    server.run()


if __name__ == "__main__":
    main()
