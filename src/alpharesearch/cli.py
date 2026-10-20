from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

from .agent import ResearchAgent
from .brain import BrainClient, BrainError
from .config import load_config
from .evaluation import EvaluationEngine
from .expression import ExpressionValidator
from .memory import ResearchMemory
from .pipeline import (
    ResearchPipeline,
    load_field_cache,
    load_operator_cache,
)

app = typer.Typer(no_args_is_help=True, help="Constrained WorldQuant BRAIN alpha research.")


def _json(value: object) -> None:
    typer.echo(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def _runtime(config_path: str | None = None):
    load_dotenv()
    cfg = load_config(config_path)
    fields = load_field_cache()
    operators = load_operator_cache()
    validator = ExpressionValidator(fields, operators, cfg["validation"])
    evaluator = EvaluationEngine(cfg["evaluation"])
    memory = ResearchMemory(cfg["project"]["database"])
    return cfg, memory, validator, evaluator


@app.command()
def init(force: bool = False) -> None:
    """Create local config and data directories."""
    Path("data").mkdir(exist_ok=True)
    if Path("config.yaml").exists() and not force:
        typer.echo("config.yaml already exists (use --force to replace)")
    else:
        shutil.copyfile("config.example.yaml", "config.yaml")
        typer.echo("created config.yaml")
    typer.echo("Next: fill .env, then run `alpha-research sync-catalog`.")


@app.command()
def validate(
    expression: str,
    config: Annotated[str | None, typer.Option("--config")] = None,
) -> None:
    """Parse and deterministically validate a FASTEXPR expression."""
    _, memory, validator, _ = _runtime(config)
    try:
        _json(validator.validate(expression).to_dict())
    finally:
        memory.close()


@app.command("sync-catalog")
def sync_catalog(
    config: Annotated[str | None, typer.Option("--config")] = None,
) -> None:
    """Fetch the account's current BRAIN fields and operators."""
    cfg = load_config(config)
    Path("data").mkdir(exist_ok=True)
    with BrainClient(
        base_url=cfg["brain"]["base_url"],
        timeout_seconds=cfg["brain"]["timeout_seconds"],
        poll_timeout_seconds=cfg["brain"]["poll_timeout_seconds"],
        max_retries=cfg["brain"]["max_retries"],
    ) as brain:
        fields = brain.data_fields(
            region=cfg["brain"]["simulation"]["region"],
            universe=cfg["brain"]["simulation"]["universe"],
            delay=cfg["brain"]["simulation"]["delay"],
            instrumentType=cfg["brain"]["simulation"]["instrumentType"],
            limit=50,
        )
        operators = brain.operators()
    Path("data/fields.json").write_text(json.dumps({"fields": fields}, indent=2), encoding="utf-8")
    Path("data/operators.json").write_text(json.dumps(operators, indent=2), encoding="utf-8")
    typer.echo(f"saved {len(fields)} fields and operator catalog")


@app.command()
def run(
    count: Annotated[int, typer.Option(min=1)] = 5,
    focus: str = "",
    notes_file: Annotated[Path | None, typer.Option("--notes")] = None,
    no_simulate: bool = False,
    config: Annotated[str | None, typer.Option("--config")] = None,
) -> None:
    """Generate, validate, optionally simulate, evaluate, and persist candidates."""
    cfg, memory, validator, evaluator = _runtime(config)
    notes = notes_file.read_text(encoding="utf-8") if notes_file else ""
    brain = None
    try:
        if not no_simulate:
            brain = BrainClient(
                base_url=cfg["brain"]["base_url"],
                timeout_seconds=cfg["brain"]["timeout_seconds"],
                poll_timeout_seconds=cfg["brain"]["poll_timeout_seconds"],
                max_retries=cfg["brain"]["max_retries"],
            )
        agent = ResearchAgent(
            model=cfg["research"]["model"],
            validator=validator,
            memory=memory,
            field_catalog=sorted(validator.fields),
            program_path=cfg["project"]["program"],
        )
        pipeline = ResearchPipeline(cfg, memory, validator, evaluator, brain)
        _json(
            pipeline.run(
                agent,
                count=count,
                paper_notes=notes,
                focus=focus,
                simulate=not no_simulate,
            )
        )
    finally:
        if brain:
            brain.close()
        memory.close()


@app.command()
def ready(
    limit: int = 20,
    config: Annotated[str | None, typer.Option("--config")] = None,
) -> None:
    """List candidates that pass local gates and latest stored platform checks."""
    _, memory, _, _ = _runtime(config)
    try:
        _json(memory.ready_candidates(limit))
    finally:
        memory.close()


@app.command()
def submit(
    candidate_id: int,
    approve: Annotated[
        bool,
        typer.Option("--approve", help="Required explicit human approval."),
    ] = False,
    config: Annotated[str | None, typer.Option("--config")] = None,
) -> None:
    """Re-check and submit one eligible candidate; never runs automatically."""
    if not approve:
        raise typer.BadParameter("submission requires --approve")
    cfg, memory, _, _ = _runtime(config)
    try:
        candidate = memory.get_candidate(candidate_id)
        if not candidate:
            raise typer.BadParameter(f"candidate {candidate_id} not found")
        if not candidate.get("eligible") or not candidate.get("alpha_id"):
            raise typer.BadParameter("candidate is not stored as eligible with an alpha id")
        if cfg["submission"].get("automatic_submit"):
            typer.echo("Warning: automatic_submit is ignored; explicit approval is still required.")
        with BrainClient(
            base_url=cfg["brain"]["base_url"],
            timeout_seconds=cfg["brain"]["timeout_seconds"],
            poll_timeout_seconds=cfg["brain"]["poll_timeout_seconds"],
            max_retries=cfg["brain"]["max_retries"],
        ) as brain:
            latest_checks = brain.check_alpha(candidate["alpha_id"])
            failed = [
                check
                for check in latest_checks
                if str(check.get("status") or check.get("result") or "").upper()
                in {"FAIL", "FAILED", "ERROR", "REJECTED"}
                or check.get("pass") is False
            ]
            if failed:
                raise BrainError(f"latest platform checks failed: {failed}")
            result = brain.submit_alpha(candidate["alpha_id"])
        memory.event(
            "submitted",
            {"alpha_id": candidate["alpha_id"], "result": result},
            candidate_id=candidate_id,
        )
        _json(result)
    finally:
        memory.close()


if __name__ == "__main__":
    app()
