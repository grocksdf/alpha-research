"""Bulk candidate ingestion. Generation is separate from BRAIN simulation execution."""
from __future__ import annotations

import json
from pathlib import Path
from .common import ResearchError


def read_candidates(path):
    """Input must be a bounded list of research hypotheses; no executable code."""
    path = Path(path)
    if path.stat().st_size > 2_000_000:
        raise ResearchError("Candidate batch exceeds 2 MB")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not 1 <= len(data) <= 1000:
        raise ResearchError("Candidate file must be a non-empty JSON list (max 1000)")
    allowed = {"expression", "hypothesis", "source", "parent_id",'parent_intent_id'}
    required = {"expression", "hypothesis", "source"}
    for index, row in enumerate(data):
        if not isinstance(row, dict) or not required.issubset(row) or not set(row) <= allowed:
            raise ResearchError(f"Candidate #{index+1} must contain expression/hypothesis/source only, with optional parent_id")
        if any(not isinstance(row[x], str) or not row[x].strip() for x in required):
            raise ResearchError(f"Candidate #{index+1} contains empty or invalid text")
        if "parent_id" in row and row["parent_id"] is not None and not isinstance(row["parent_id"], str):
            raise ResearchError(f"Candidate #{index+1} has invalid parent_id")
    return data
