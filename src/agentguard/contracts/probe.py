from typing import Literal

from pydantic import Field, model_validator
from pydantic.types import JsonValue

from agentguard.contracts.base import ContractModel


class BinaryProbeTrainRequest(ContractModel):
    """Train/test a binary linear probe with case-family-isolated splitting."""
    schema_version: Literal["0.1"] = "0.1"
    features: list[list[float]] = Field(min_length=4, max_length=100000)
    labels: list[int] = Field(min_length=4, max_length=100000)
    case_families: list[str] = Field(min_length=4, max_length=100000)
    feature_names: list[str] | None = None
    dataset_name: str = "unspecified"
    dataset_version: str = "unspecified"
    label_source: str = "unspecified"
    representation_source: str = "caller_supplied_numeric_features"
    test_fraction: float = Field(default=0.2, ge=0.05, le=0.5)
    validation_fraction: float = Field(default=0.15, ge=0.05, le=0.3)
    seed: int = 7
    learning_rate: float = Field(default=0.1, gt=0.0, le=10.0)
    epochs: int = Field(default=800, ge=1, le=10000)
    l2: float = Field(default=0.01, ge=0.0, le=100.0)
    threshold: float | None = Field(default=None, gt=0.0, lt=1.0)

    @model_validator(mode="after")
    def validate_dataset_shape(self):
        if len(self.features) != len(self.labels) or len(self.features) != len(self.case_families):
            raise ValueError("features, labels and case_families must have equal lengths")
        if set(self.labels) != {0, 1}:
            raise ValueError("labels must contain both 0 and 1")
        if len({len(row) for row in self.features}) != 1 or not self.features[0]:
            raise ValueError("feature vectors must have the same non-zero dimension")
        if len(set(self.case_families)) < 2:
            raise ValueError("at least two distinct case families are required")
        if self.feature_names is not None and len(self.feature_names) != len(self.features[0]):
            raise ValueError("feature_names length must match feature dimension")
        return self


class BinaryProbeTrainingReport(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    model_artifact_id: str
    model_type: str = "binary_logistic_linear_probe"
    dataset_sha256: str
    dataset_name: str = "unspecified"
    dataset_version: str = "unspecified"
    label_source: str = "unspecified"
    representation_source: str = "caller_supplied_numeric_features"
    seed: int
    train_samples: int = Field(ge=1)
    validation_samples: int = Field(ge=1)
    test_samples: int = Field(ge=1)
    train_group_count: int = Field(ge=1)
    validation_group_count: int = Field(ge=1)
    test_group_count: int = Field(ge=1)
    threshold: float = Field(gt=0.0, lt=1.0)
    validation_metrics: dict[str, JsonValue]
    group_split_verified: bool = True
    metrics: dict[str, JsonValue]
    notes: list[str] = Field(default_factory=list)


class BinaryProbePredictRequest(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    model_artifact_id: str = Field(min_length=64, max_length=64)
    features: list[list[float]] = Field(min_length=1, max_length=100000)

    @model_validator(mode="after")
    def validate_feature_rows(self):
        if not self.features[0] or len({len(row) for row in self.features}) != 1:
            raise ValueError("feature vectors must have the same non-zero dimension")
        return self


class BinaryProbePredictionReport(ContractModel):
    schema_version: Literal["0.1"] = "0.1"
    model_artifact_id: str
    scores: list[float]
    predictions: list[int]
    threshold: float = Field(gt=0.0, lt=1.0)
    evidence_mode: Literal["observed", "measured"] = "observed"
    limitations: list[str] = Field(default_factory=list)
