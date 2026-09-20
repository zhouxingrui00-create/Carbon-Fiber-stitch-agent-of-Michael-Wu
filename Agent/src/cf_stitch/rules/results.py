"""Shared, traceable result contract for deterministic checks."""

from enum import Enum
from typing import Any

from pydantic import Field, model_validator

from cf_stitch.domain.schemas import DomainModel, SourceReference


class Status(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"
    UNKNOWN = "UNKNOWN"


class RuleResult(DomainModel):
    code: str
    status: Status
    message: str
    field: str | None = None
    value: float | list[float] | None = None
    unit: str | None = None
    provenance: str = "deterministic_rule"
    source_refs: list[SourceReference] = Field(default_factory=list)
    basis: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def no_unavailable_numbers(self):
        if self.status in {Status.BLOCK, Status.UNKNOWN} and self.value is not None:
            raise ValueError("阻断或未知结果不得携带可用计算值")
        return self


def aggregate_status(results: list[RuleResult]) -> Status:
    """BLOCK > UNKNOWN > WARN > PASS; unknown cannot be hidden by a warning."""
    for status in (Status.BLOCK, Status.UNKNOWN, Status.WARN):
        if any(result.status == status for result in results):
            return status
    return Status.PASS if results else Status.UNKNOWN
