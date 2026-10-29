"""GSEI增量接口；使用原API认证、审计与研究执行服务。"""
from fastapi import Header, HTTPException, Request

from agentguard.contracts.graded_security import (
    DecisionExecution,
    DecisionPreview,
    SecurityProfile,
    ValueEvent,
)
from agentguard.services.graded_registry import GradedRegistry
from agentguard.services.graded_security import validate_targets
from agentguard.services.intervention_decision import decide


def register_graded_routes(app, service_factory, authorize, audit):
    def registry():
        return GradedRegistry(app.state.artifacts)

    def require_study(study_id):
        if app.state.research_registry.get(study_id) is None:
            raise HTTPException(404, "Study not found")

    def graph_for(service, study_id, workflow):
        from agentguard.services.experiment_views import project_graph
        result, events = service.events(study_id, workflow)
        return project_graph(study_id, result["run"]["run_id"], "live_steganography", events), events

    @app.get("/v1/studies/{study_id}/security-profile")
    def profiles(study_id: str):
        require_study(study_id)
        return {"schema_version": "gsei.profiles/1", "items": registry().list_profiles(study_id), "missing_level": "UNKNOWN"}

    @app.post("/v1/studies/{study_id}/security-profile", status_code=201)
    def register_profile(study_id: str, payload: SecurityProfile, request: Request,
                         x_agentguard_operator_token: str | None = Header(default=None),
                         x_agentguard_token: str | None = Header(default=None)):
        authorize(x_agentguard_operator_token or x_agentguard_token, None)
        require_study(study_id)
        if payload.study_id != study_id:
            raise HTTPException(422, "PROFILE_STUDY_MISMATCH")
        try:
            graph, _ = graph_for(service_factory(), study_id, payload.workflow_id)
            if any(n.agent_id not in {v["id"] for v in graph["nodes"]} for n in payload.nodes) or any(e.edge_id not in {e["id"] for e in graph["communication_edges"]} for e in payload.channels):
                raise ValueError("PROFILE_TARGET_NOT_OBSERVED")
            audit(request, payload.model_dump(), approval_valid=False)
            return registry().save("profile", study_id, payload.model_dump())
        except (KeyError, ValueError, RuntimeError) as exc:
            raise HTTPException(422, "PROFILE_OR_SOURCE_NOT_VERIFIED") from exc

    @app.post("/v1/studies/{study_id}/value-events", status_code=201)
    def annotate(study_id: str, payload: ValueEvent, workflow_id: str, profile_artifact_id: str,
                 request: Request, x_agentguard_operator_token: str | None = Header(default=None),
                 x_agentguard_token: str | None = Header(default=None)):
        authorize(x_agentguard_operator_token or x_agentguard_token, None)
        require_study(study_id)
        try:
            profile = SecurityProfile.model_validate(registry().read("profile", study_id, profile_artifact_id))
            graph, events = graph_for(service_factory(), study_id, workflow_id)
            validate_targets(profile, payload, graph, events)
            audit(request, payload.model_dump(), approval_valid=False)
            return registry().save("event", study_id, payload.model_dump())
        except (KeyError, ValueError, RuntimeError, FileNotFoundError) as exc:
            raise HTTPException(422, "EVENT_OR_EVIDENCE_NOT_VERIFIED") from exc

    @app.post("/v1/studies/{study_id}/intervention-decisions/preview")
    def preview(study_id: str, payload: DecisionPreview, request: Request,
                x_agentguard_operator_token: str | None = Header(default=None),
                x_agentguard_token: str | None = Header(default=None)):
        require_study(study_id)
        try:
            may_execute = True
            try:
                authorize(x_agentguard_operator_token or x_agentguard_token, None)
            except HTTPException:
                may_execute = False
            profile = SecurityProfile.model_validate(registry().read("profile", study_id, payload.profile_artifact_id))
            event = ValueEvent.model_validate(registry().read("event", study_id, payload.event_artifact_id))
            decision = decide(service_factory(), profile, event, payload.workflow_id,
                              may_execute=may_execute, batch_id=payload.batch_id)
            result = decision.model_copy(update={"profile_artifact_id": payload.profile_artifact_id,
                                                "event_artifact_id": payload.event_artifact_id}).model_dump()
            audit(request, payload.model_dump(), approval_valid=False)
            return registry().save("decision", study_id, result)
        except (KeyError, ValueError, RuntimeError, FileNotFoundError) as exc:
            raise HTTPException(422, "DECISION_INPUT_NOT_VERIFIED") from exc

    @app.get("/v1/studies/{study_id}/intervention-decisions/{decision_id}")
    def read_decision(study_id: str, decision_id: str):
        require_study(study_id)
        try:
            return registry().read("decision", study_id, decision_id)
        except (KeyError, ValueError, RuntimeError, FileNotFoundError) as exc:
            raise HTTPException(404, "Decision not found or not verified") from exc

    @app.post("/v1/studies/{study_id}/intervention-decisions/{decision_id}/execute")
    def execute(study_id: str, decision_id: str, payload: DecisionExecution, request: Request,
                x_agentguard_operator_token: str | None = Header(default=None),
                x_agentguard_token: str | None = Header(default=None)):
        authorize(x_agentguard_operator_token or x_agentguard_token, None)
        require_study(study_id)
        try:
            prior = registry().read("decision", study_id, decision_id)
            selected = next((c for c in prior["candidates"] if c["arm_id"] == payload.arm_id and c["feasible"]), None)
            if selected is None:
                raise ValueError("ACTION_NOT_FEASIBLE")
            service = service_factory()
            current = service.execution_contract(study_id, payload.arm_id)
            if current["status"] != "verified_frozen_contract" or current["contract"] != selected["execution_contract"]:
                raise ValueError("EXECUTION_CONTRACT_CHANGED")
            audit(request, {"decision_id": decision_id, **payload.model_dump()}, approval_valid=False)
            if payload.paired_batch:
                batch = service.submit_batch(study_id, f"gsei-{decision_id}", replicate_id=f"gsei-{decision_id}")
                member = next(i for i in batch["items"] if i["arm_id"] == payload.arm_id)
                result = {**member["submission"], "workflow_id": member["workflow_id"], "batch": batch}
            else:
                result = service.submit(study_id, payload.arm_id, f"gsei-{decision_id}:{payload.arm_id}")
            receipt = registry().save("execution", study_id, {"decision_id": decision_id,
                "arm_id": payload.arm_id, "submission": result, "causal_status": "NOT_VERIFIED"})
            return {**result, "decision_id": decision_id, "execution_receipt": receipt["artifact_id"]}
        except (KeyError, ValueError, RuntimeError, FileNotFoundError) as exc:
            raise HTTPException(422, "DECISION_EXECUTION_NOT_VERIFIED") from exc
