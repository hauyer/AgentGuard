from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Base contract: strict fields, no accidental schema drift."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)
