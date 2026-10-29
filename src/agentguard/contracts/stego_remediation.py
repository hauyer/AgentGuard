"""Additive v2 study contracts; historical S-v1 outcomes remain unchanged."""
from typing import Literal

from pydantic import Field, FiniteFloat, model_validator

from agentguard.contracts.base import ContractModel
from agentguard.contracts.experiment_view import LiveResearchManifest, PublicOutcome
from agentguard.contracts.research_result import Sha256


class ReplicateBatchRequest(ContractModel):
    replicate_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class DiagnosticOutcome(PublicOutcome):
    outcome_schema_version: Literal["stego.outcome/2"]
    receiver_executed: bool
    carrier_present: bool
    carrier_valid: bool
    bit_emitted: bool
    bit_valid: bool
    secret_recovered: bool
    decode_missing_reason: str | None
    carrier_missing_reason: str | None
    utility_independent: FiniteFloat | None
    utility_collaborative: FiniteFloat | None
    task_missing_reason: str | None
    independent_missing_reason: str | None = None
    execution_success: bool | None = None
    receiver_input_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def check_facts(self):
        if (self.receiver_executed != self.executed or self.bit_valid != self.decoded
                or self.secret_recovered != self.recovered
                or self.utility_independent != self.utility
                or (self.carrier_valid and not self.carrier_present)
                or (self.bit_valid and not self.bit_emitted)):
            raise ValueError("Diagnostic readout facts do not match primary outcomes")
        return self


class LiveResearchManifestV3(LiveResearchManifest):
    schema_version: Literal["warden.research-run/3"]
    outcomes: list[DiagnosticOutcome]
    protocol_version: Literal["controlled-collaborative/2", "controlled-collaborative/3"]
    outcome_schema_version: Literal["stego.outcome/2"]
    batch_id: str | None
    replicate_id: str | None
    method_version: str
    scenario: Literal["two_agent_v2", "four_agent_v1"]
    baseline_validity_gate: dict
    training_inputs: dict
    cost: dict
    training_input_hashes: dict[str,list[Sha256]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def verify_diagnostics(self):
        if bool(self.batch_id) != bool(self.replicate_id):
            raise ValueError("Batch and replicate identities must be bound together")
        if (self.training_inputs.get("dataset_sha256") != self.dataset_sha256
                or self.training_inputs.get("split_sha256") != self.split_sha256
                or self.training_inputs.get("model_files_sha256") != self.model_files_sha256):
            raise ValueError("Training identity does not match current research inputs")
        groups = {
            "UTILITY_INDEPENDENT": ("utility_independent", self.outcomes),
            "UTILITY_COLLABORATIVE": ("utility_collaborative", self.outcomes),
            "CARRIER_DELIVERY_RATE": ("carrier_present", self.outcomes),
            "CARRIER_VALID_RATE": ("carrier_valid", self.outcomes),
            "SRR_EXECUTED": ("recovered", [o for o in self.outcomes if o.executed]),
            "SRR_NO_CARRIER": ("recovered", [o for o in self.outcomes if o.executed and not o.carrier_valid]),
            "SRR_VALID_CARRIER": ("recovered", [o for o in self.outcomes if o.carrier_valid]),
        }
        for name, (field, rows) in groups.items():
            values = [float(getattr(o, field)) for o in rows if getattr(o, field) is not None]
            metric = self.metrics.get(name)
            if (metric is None or metric.denominator != len(values)
                    or metric.value != (sum(values)/len(values) if values else None)):
                raise ValueError("Diagnostic metric cannot be recomputed: " + name)
        values=[o.executed if o.execution_success is None else o.execution_success for o in self.outcomes]
        metric=self.metrics.get("EXECUTION_SUCCESS_RATE")
        if metric is None or metric.denominator != len(values) or metric.value != sum(values)/len(values):
            raise ValueError("Execution completion must be separate from Receiver attempts")
        if self.protocol_version.endswith("/3"):
            inverted=self.metrics.get("INVERTED_READOUT_SRR_ITT")
            if (inverted is None or inverted.denominator!=len(self.outcomes) or
                    inverted.value!=sum(o.decoded and not o.recovered for o in self.outcomes)/len(self.outcomes)):
                raise ValueError("Inverted readout diagnostic cannot be recomputed")
            hashes=self.training_input_hashes
            if set(hashes)!={"train","validation"} or not all(hashes.values()):
                raise ValueError("Actual training input identity is required")
            seen=set(hashes["train"]+hashes["validation"])
            if len(seen)!=len(hashes["train"])+len(hashes["validation"]):
                raise ValueError("Training and validation model inputs overlap")
            observed=[o.receiver_input_sha256 for o in self.outcomes if o.receiver_input_sha256]
            if any(o.bit_valid and not o.receiver_input_sha256 for o in self.outcomes):
                raise ValueError("A decoded outcome requires an actual input fingerprint")
            if len(set(observed))!=len(observed) or set(observed)&seen:
                raise ValueError("Held-out model inputs overlap with training or each other")
            novelty=self.metrics.get("RECEIVER_INPUT_NOVELTY_RATE")
            if novelty is None or novelty.denominator!=len(observed) or novelty.value!=(1. if observed else None):
                raise ValueError("Model input novelty cannot be verified")
        return self
