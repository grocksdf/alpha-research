from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from typing import Any

from .models import EvaluationResult, SimulationResult, ValidationResult


def _walk_values(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_values(child, (*path, str(key).lower()))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_values(child, (*path, str(index)))
    else:
        yield path, value


def _find_number(payload: Any, *names: str) -> float | None:
    wanted = {name.lower() for name in names}
    for path, value in _walk_values(payload):
        if path and path[-1] in wanted and isinstance(value, (int, float)):
            return float(value)
    return None


def _year_metrics(payload: Any) -> list[float]:
    results: list[float] = []
    for path, value in _walk_values(payload):
        if not isinstance(value, (int, float)):
            continue
        joined = ".".join(path)
        if any(str(year) in joined for year in range(1990, 2101)) and path[-1] in {
            "sharpe",
            "returns",
            "return",
        }:
            results.append(float(value))
    return results


def _checks_pass(checks: list[dict[str, Any]]) -> tuple[bool, list[str], dict[str, float]]:
    failures: list[str] = []
    correlations: dict[str, float] = {}
    for check in checks:
        name = str(check.get("name") or check.get("code") or check.get("id") or "UNKNOWN")
        status = str(check.get("result") or check.get("status") or "").upper()
        value = check.get("value")
        if isinstance(value, (int, float)):
            lowered = name.lower()
            if "self" in lowered and "corr" in lowered:
                correlations["self_correlation"] = float(value)
            if ("prod" in lowered or "production" in lowered) and "corr" in lowered:
                correlations["production_correlation"] = float(value)
        if status in {"FAIL", "FAILED", "ERROR", "REJECTED"} or check.get("pass") is False:
            failures.append(name)
    return not failures, failures, correlations


class EvaluationEngine:
    """Fixed deterministic evaluator. The Research Agent never receives a mutation handle."""

    def __init__(self, rules: dict[str, Any]):
        self.rules = json.loads(json.dumps(rules))
        frozen = json.dumps(self.rules, sort_keys=True, separators=(",", ":"))
        self.version_hash = hashlib.sha256(frozen.encode()).hexdigest()[:16]

    @staticmethod
    def _bounded(value: float | None, low: float, high: float) -> float:
        if value is None or not math.isfinite(value):
            return 0.0
        if high <= low:
            return float(value >= high)
        return max(0.0, min(1.0, (value - low) / (high - low)))

    def evaluate(
        self,
        validation: ValidationResult,
        simulation: SimulationResult,
        family_count: int = 0,
    ) -> EvaluationResult:
        reasons: list[str] = []
        if not validation.valid:
            reasons.append("deterministic validation failed")

        alpha = simulation.alpha
        sharpe = _find_number(alpha, "sharpe")
        fitness = _find_number(alpha, "fitness")
        turnover = _find_number(alpha, "turnover")
        drawdown = _find_number(alpha, "drawdown")
        years = _year_metrics(alpha)
        checks_ok, failed_checks, correlations = _checks_pass(simulation.checks)

        metrics: dict[str, Any] = {
            "sharpe": sharpe,
            "fitness": fitness,
            "turnover": turnover,
            "drawdown": drawdown,
            "year_values": years,
            "positive_year_fraction": (
                sum(value > 0 for value in years) / len(years) if years else None
            ),
            "failed_checks": failed_checks,
            **correlations,
        }

        gates = [
            (sharpe is not None and sharpe >= self.rules["min_sharpe"], "Sharpe below gate"),
            (fitness is not None and fitness >= self.rules["min_fitness"], "Fitness below gate"),
            (
                turnover is not None
                and self.rules["min_turnover"] <= turnover <= self.rules["max_turnover"],
                "Turnover outside gate",
            ),
            (
                drawdown is None or drawdown <= self.rules["max_drawdown"],
                "Drawdown above gate",
            ),
            (checks_ok, f"Platform checks failed: {', '.join(failed_checks)}"),
        ]
        if years:
            gates.extend(
                [
                    (len(years) >= self.rules["min_years"], "Insufficient yearly observations"),
                    (
                        metrics["positive_year_fraction"]
                        >= self.rules["min_positive_year_fraction"],
                        "Year stability below gate",
                    ),
                ]
            )
        for key, maximum in (
            ("self_correlation", self.rules["max_self_correlation"]),
            ("production_correlation", self.rules["max_production_correlation"]),
        ):
            if key in metrics:
                gates.append((metrics[key] <= maximum, f"{key} above gate"))
        reasons.extend(message for passed, message in gates if not passed)

        turnover_center = (
            float(self.rules["min_turnover"]) + float(self.rules["max_turnover"])
        ) / 2
        turnover_score = max(
            0.0,
            1
            - abs((turnover if turnover is not None else 99) - turnover_center)
            / max(turnover_center, 1e-9),
        )
        year_score = (
            float(metrics["positive_year_fraction"])
            if metrics["positive_year_fraction"] is not None
            else 0.5
        )
        novelty_score = 1 / (1 + max(0, family_count))
        components = {
            "sharpe": self._bounded(sharpe, self.rules["min_sharpe"], 3.0),
            "fitness": self._bounded(fitness, self.rules["min_fitness"], 3.0),
            "turnover": turnover_score,
            "drawdown": 1 - self._bounded(drawdown, 0.0, self.rules["max_drawdown"])
            if drawdown is not None
            else 0.5,
            "year_stability": year_score,
            "novelty": novelty_score,
        }
        weights = self.rules["weights"]
        score = 100 * sum(components[key] * float(weights[key]) for key in weights)
        eligible = validation.valid and not reasons
        return EvaluationResult(eligible, round(score, 4), reasons, metrics, components)
