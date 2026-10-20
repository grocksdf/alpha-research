from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Candidate:
    expression: str
    hypothesis: str
    family: str
    rationale: str = ""
    parent_id: int | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "error"
    position: int | None = None


@dataclass(slots=True)
class ValidationResult:
    valid: bool
    canonical: str | None
    fields: list[str]
    operators: list[str]
    node_count: int
    depth: int
    family_hash: str | None
    issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["issues"] = [asdict(issue) for issue in self.issues]
        return value


@dataclass(slots=True)
class SimulationResult:
    simulation_url: str
    alpha_id: str
    alpha: dict[str, Any]
    checks: list[dict[str, Any]]
    raw_simulation: dict[str, Any]


@dataclass(slots=True)
class EvaluationResult:
    eligible: bool
    score: float
    reasons: list[str]
    metrics: dict[str, Any]
    components: dict[str, float]
