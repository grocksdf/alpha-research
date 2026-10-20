from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agent import ResearchAgent
from .brain import BrainClient
from .evaluation import EvaluationEngine
from .expression import DEFAULT_FIELDS, ExpressionValidator, operator_catalog_from_api
from .memory import ResearchMemory
from .models import Candidate


def load_field_cache(path: str | Path = "data/fields.json") -> list[str]:
    cache = Path(path)
    if not cache.exists():
        return sorted(DEFAULT_FIELDS)
    payload = json.loads(cache.read_text(encoding="utf-8"))
    values = payload.get("fields", payload) if isinstance(payload, dict) else payload
    result: list[str] = []
    if isinstance(values, list):
        for item in values:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                value = item.get("id") or item.get("name")
                if value:
                    result.append(str(value))
    return sorted(set(result)) or sorted(DEFAULT_FIELDS)


def load_operator_cache(path: str | Path = "data/operators.json") -> dict[str, dict[str, Any]]:
    cache = Path(path)
    if not cache.exists():
        return operator_catalog_from_api([])
    return operator_catalog_from_api(json.loads(cache.read_text(encoding="utf-8")))


class ResearchPipeline:
    def __init__(
        self,
        config: dict[str, Any],
        memory: ResearchMemory,
        validator: ExpressionValidator,
        evaluator: EvaluationEngine,
        brain: BrainClient | None,
    ):
        self.config = config
        self.memory = memory
        self.validator = validator
        self.evaluator = evaluator
        self.brain = brain

    def run(
        self,
        agent: ResearchAgent,
        count: int,
        paper_notes: str = "",
        focus: str = "",
        simulate: bool = True,
    ) -> dict[str, Any]:
        budget = min(count, int(self.config["research"]["max_simulations_per_run"]))
        run_id = self.memory.create_run(self.config, self.evaluator.version_hash, focus)
        report: dict[str, Any] = {
            "run_id": run_id,
            "proposed": 0,
            "duplicates": 0,
            "invalid": 0,
            "simulated": 0,
            "ready": 0,
            "failures": [],
        }
        try:
            candidates = agent.propose(count=budget, paper_notes=paper_notes, focus=focus)
            report["proposed"] = len(candidates)
            for candidate in candidates:
                self._process_candidate(run_id, candidate, report, simulate)
            self.memory.finish_run(run_id, "COMPLETE")
        except Exception as exc:
            self.memory.event("run_failed", {"error": str(exc)}, run_id=run_id)
            self.memory.finish_run(run_id, "FAILED")
            raise
        return report

    def _process_candidate(
        self,
        run_id: int,
        candidate: Candidate,
        report: dict[str, Any],
        simulate: bool,
    ) -> None:
        validation = self.validator.validate(candidate.expression)
        candidate_id, inserted = self.memory.add_candidate(run_id, candidate, validation)
        if not inserted:
            report["duplicates"] += 1
            return
        if not validation.valid:
            report["invalid"] += 1
            return
        if not simulate:
            return
        if self.brain is None:
            raise RuntimeError("BRAIN client is required when simulate=True")
        try:
            result = self.brain.simulate(
                validation.canonical or candidate.expression,
                self.config["brain"]["simulation"],
            )
            self.memory.record_simulation(candidate_id, result=result)
            report["simulated"] += 1
            family_count = self.memory.connection.execute(
                "SELECT COUNT(*) AS n FROM candidates WHERE family_hash=? AND id<>?",
                (validation.family_hash, candidate_id),
            ).fetchone()["n"]
            evaluation = self.evaluator.evaluate(validation, result, int(family_count))
            self.memory.record_evaluation(candidate_id, evaluation, self.evaluator.version_hash)
            if evaluation.eligible:
                report["ready"] += 1
        except Exception as exc:  # noqa: BLE001 - persist every experiment failure
            self.memory.record_simulation(candidate_id, error=str(exc))
            report["failures"].append({"candidate_id": candidate_id, "error": str(exc)})
